"""Database connection and helpers for xiaogu."""
import hashlib
import json
import os
import uuid
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

SCORING_CONFIG_DEFAULTS: Dict[str, str] = {
    # Empty = no weekday ban. Explicit e.g. "0,4" bans Mon/Fri. Never treat '' as missing.
    "weekday_blocklist": "",
    "max_score_cap": "88",
    "instant_momentum_min_confirmations": "2",
    "stale_repeat_window_days": "5",
    "stale_decay_factor": "0.65",
    "l2_limit_strength_bonus": "2.0",
    "sector_catalyst_penalty": "1.0",
    "near_limit_l2_exemption": "true",
    "evidence_catalyst_boost_weight": "0.5",
    "evidence_limitup_momentum_weight": "0.7",
    "evidence_broken_limit_penalty_weight": "1.5",
    "evidence_consecutive_limit_bonus_weight": "0.5",
    "evidence_yesterday_limit_bonus_weight": "0.3",
    "evidence_popularity_boost_weight": "1.0",
    "evidence_board_momentum_weight": "0.5",
    "evidence_sector_flow_weight": "0.5",
    "evidence_concept_flow_weight": "0.5",
    "evidence_quote_recheck_weight": "0.3",
    "evidence_fund_recheck_weight": "0.3",
    "evidence_lhb_recheck_weight": "0.4",
    "evidence_announcement_recheck_weight": "0.3",
    "evidence_intraday_replay_weight": "0.2",
    "evidence_margin_risk_weight": "1.0",
    "evidence_block_trade_weight": "0.5",
    "evidence_lockup_risk_weight": "1.5",
    "evidence_shareholder_signal_weight": "0.5",
    "evidence_research_rating_weight": "0.5",
    "evidence_earnings_signal_weight": "0.8",
    "evidence_ipo_pressure_weight": "0.3",
    "evidence_halt_block_weight": "5.0",
    "evidence_directory_content_weight": "0.2",
    "evidence_historical_risk_weight": "0.5",
}

_SCORING_CONFIG_CACHE: Dict[str, Any] | None = None

MAINBOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
COHORT_FULL_CHAIN_COMPLETE = "FULL_CHAIN_COMPLETE"
COHORT_TRANSITION_RECONSTRUCTABLE = "TRANSITION_RECONSTRUCTABLE"
COHORT_DB_ONLY_LEGACY = "DB_ONLY_LEGACY"
COHORT_NON_MAINBOARD_EXCLUDED = "NON_MAINBOARD_EXCLUDED"
COHORT_NO_RETURN_YET = "NO_RETURN_YET"
COHORT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
LIMITUP_GENE_SHADOW_SIGNALS = (
    "previous_limitup",
    "near_limitup_close",
    "first_board_gene",
    "broken_board_repair",
    "sector_limitup_cluster",
    "high_turnover_continuation",
)


def is_mainboard_symbol(symbol: Any) -> bool:
    """Return whether a symbol belongs to the current main-board universe."""
    code = str(symbol or "").strip().zfill(6)
    return code.startswith(MAINBOARD_PREFIXES)


def _json_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return bool(str(value).strip())


def _candidate_snapshot_value(candidate: Dict[str, Any], key: str) -> Any:
    if key in candidate and candidate[key] is not None:
        return candidate[key]
    for source_name in (
        "candidate_features", "factor_snapshot", "auxiliary_evidence_snapshot",
        "ranking_basis", "raw_json",
    ):
        source = candidate.get(source_name)
        if isinstance(source, dict) and key in source and source[key] is not None:
            return source[key]
    return None


def limitup_gene_signal_values(candidate: Dict[str, Any]) -> Dict[str, bool]:
    """Return the single pre-decision definition for persisted gene signals."""
    value = lambda key: _candidate_snapshot_value(candidate, key)
    as_number = lambda raw: float(raw or 0) if isinstance(raw, (int, float, bool)) else 0.0
    return {
        "previous_limitup": bool(value("previous_limitup") or value("yesterday_limitup_gene_evidence")),
        "near_limitup_close": bool(value("near_limitup_close") or as_number(value("close_position_score")) >= 0.8),
        "first_board_gene": bool(value("first_board_gene")),
        "broken_board_repair": bool(value("broken_board_repair")),
        "sector_limitup_cluster": bool(
            value("sector_limitup_cluster") or as_number(value("sector_yesterday_limitup_gene_proxy")) > 0
        ),
        "high_turnover_continuation": bool(
            value("high_turnover_continuation")
            or (as_number(value("turnover_rate")) > 0 and as_number(value("continuation_gene_score")) > 0)
        ),
    }


def _candidate_has_reconstructable_source(candidate: Dict[str, Any]) -> bool:
    return any(
        _json_has_value(candidate.get(field))
        for field in (
            "raw_json", "candidate_features", "eligibility_snapshot",
            "selection_diagnostics", "source_layers",
        )
    )


def classify_candidate_cohort(
    candidate: Dict[str, Any],
    *,
    top10_count: Optional[int] = None,
    has_return: Optional[bool] = None,
    trade_date: Any = None,
) -> Dict[str, Any]:
    """Classify one DB candidate without treating missing evidence as PASS.

    ``cohort`` is the exclusive reporting label. ``cohort_quality`` preserves
    the evidence-quality class when ``NO_RETURN_YET`` is the blocking status.
    This lets reports answer both "is it complete?" and "can it be measured?".
    """
    symbol = candidate.get("symbol")
    if not is_mainboard_symbol(symbol):
        quality = COHORT_NON_MAINBOARD_EXCLUDED
    else:
        # Full-chain evidence is about persisted decision snapshots, not pool rank.
        # Official picks and deep-pool candidates with complete snapshots must not be
        # forced into INSUFFICIENT_EVIDENCE merely because rank > 10.
        decision_evidence_fields = (
            _json_has_value(candidate.get("candidate_entry_reason")),
            _json_has_value(candidate.get("factor_snapshot")),
            _json_has_value(candidate.get("auxiliary_evidence_snapshot")),
            _json_has_value(candidate.get("ranking_basis")),
        )
        pool_ready = (top10_count or 0) >= 10 or bool(
            candidate.get("is_official_pick")
            or str(candidate.get("selection_outcome") or "").upper() == "OFFICIAL_PICK"
            or str(candidate.get("decision") or "").upper() == "PAPER_PICK"
        )
        complete_fields = decision_evidence_fields + (pool_ready,)
        provenance = candidate.get("reconstruction_provenance") or {}
        reconstructed_snapshot = any(
            isinstance(provenance.get(field), dict) and provenance[field].get("reconstructed")
            for field in ("candidate_entry_reason", "factor_snapshot", "auxiliary_evidence_snapshot", "ranking_basis")
        )
        # Any row in the historical reconstruction window is transition data,
        # even when one or more snapshots pre-existed the backfill. This keeps
        # the current full-chain benchmark from absorbing legacy quality.
        if str(trade_date or "") < "2026-07-10" and _json_has_value(provenance):
            reconstructed_snapshot = True
        if all(complete_fields) and not reconstructed_snapshot:
            quality = COHORT_FULL_CHAIN_COMPLETE
        elif trade_date and str(trade_date) >= "2026-06-20" and _candidate_has_reconstructable_source(candidate):
            quality = COHORT_TRANSITION_RECONSTRUCTABLE
        elif _json_has_value(candidate.get("raw_json")) or _json_has_value(candidate.get("candidate_features")):
            quality = COHORT_DB_ONLY_LEGACY
        else:
            quality = COHORT_INSUFFICIENT_EVIDENCE

    status_flags: List[str] = []
    if has_return is False:
        status_flags.append(COHORT_NO_RETURN_YET)
    exclusive = quality
    if quality not in (COHORT_NON_MAINBOARD_EXCLUDED,) and has_return is False:
        exclusive = COHORT_NO_RETURN_YET
    return {
        "cohort": exclusive,
        "cohort_quality": quality,
        "status_flags": status_flags,
        "is_mainboard": is_mainboard_symbol(symbol),
        "has_return": has_return,
    }


def _source_value(candidate: Dict[str, Any], field: str) -> tuple[Any, str]:
    """Pick the first non-empty reconstruction source in the agreed order."""
    source_fields = (
        ("raw_json", "daily_candidates.raw_json"),
        ("candidate_features", "candidate_features"),
        ("eligibility_snapshot", "eligibility_snapshot"),
        ("selection_diagnostics", "selection_diagnostics"),
        ("source_layers", "source_layers"),
    )
    for container_name, source_name in source_fields:
        container = candidate.get(container_name)
        if isinstance(container, dict) and _json_has_value(container.get(field)):
            return container.get(field), source_name
        if container_name == "raw_json" and isinstance(container, dict) and _json_has_value(container):
            aliases = {
                "factor_snapshot": ("factor_snapshot", "structured_components", "structured_scores"),
                "auxiliary_evidence_snapshot": ("auxiliary_evidence_snapshot", "auxiliary_evidence", "evidence"),
                "candidate_entry_reason": ("candidate_entry_reason", "entry_reason", "why_candidate"),
                "ticket_reason": ("ticket_reason", "selection_reason", "why_selected"),
                "not_selected_reason": ("not_selected_reason", "not_selected_reasons", "why_not_selected"),
                "ranking_basis": ("ranking_basis", "ranking_basis_snapshot"),
            }
            for alias in aliases.get(field, (field,)):
                if _json_has_value(container.get(alias)):
                    return container.get(alias), source_name
    return None, ""


def reconstruct_candidate_evidence(
    candidate: Dict[str, Any],
    *,
    pick: Optional[Dict[str, Any]] = None,
    return_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rebuild missing snapshots from recorded fields and attach provenance.

    The function only copies observed values. Missing announcement/news/
    yesterday-limitup evidence remains explicitly missing; no PASS is inferred.
    """
    pick = pick or {}
    return_row = return_row or {}
    sources_used: List[str] = []
    provenance: Dict[str, Any] = {}
    output: Dict[str, Any] = {}
    fields = (
        "candidate_entry_reason", "ticket_reason", "not_selected_reason",
        "factor_snapshot", "auxiliary_evidence_snapshot", "ranking_basis",
    )
    for field in fields:
        existing = candidate.get(field)
        if not _json_has_value(existing):
            existing = None
        if _json_has_value(existing):
            output[field] = existing
            provenance[field] = {
                "reconstructed": False,
                "reconstruction_source": ["daily_candidates." + field],
                "reconstruction_confidence": "HIGH",
                "missing_fields": [],
            }
            continue
        value, source = _source_value(candidate, field)
        if value is None and field in ("ticket_reason", "ranking_basis"):
            pick_value = pick.get(field)
            if _json_has_value(pick_value):
                value, source = pick_value, "picks." + field
        if value is None and field == "candidate_entry_reason":
            value = candidate.get("selection_reason") or candidate.get("selection_outcome_reason")
            source = "daily_candidates.selection_reason" if _json_has_value(value) else ""
            if not _json_has_value(value):
                value = None
        if value is None and field == "not_selected_reason":
            value = candidate.get("blockers")
            source = "daily_candidates.blockers" if _json_has_value(value) else ""
            if not _json_has_value(value):
                value = None
        if value is None and field == "candidate_entry_reason" and _json_has_value(candidate.get("rank")):
            value = ["candidate_entry_reason_not_recorded"]
            source = "daily_candidates.rank"
        if value is None and field == "not_selected_reason" and int(candidate.get("rank") or 999999) > 1:
            value = ["not_selected_reason_not_recorded"]
            source = "daily_candidates.rank"
        if value is None and field == "factor_snapshot":
            observed = candidate.get("candidate_features") or candidate.get("raw_json") or {}
            if isinstance(observed, dict) and observed:
                value = {"observed_recorded_features": observed, "evidence_status": "RECONSTRUCTED_FROM_RECORDED_FEATURES"}
                source = "candidate_features" if candidate.get("candidate_features") else "daily_candidates.raw_json"
        if value is None and field == "auxiliary_evidence_snapshot":
            value = {
                "announcements": {"status": "MISSING", "reason": "historical_evidence_not_recorded"},
                "news": {"status": "MISSING", "reason": "historical_evidence_not_recorded"},
                "yesterday_limitup_gene": {"status": "MISSING", "reason": "historical_evidence_not_recorded"},
                "risk_notices": {"status": "MISSING", "reason": "historical_evidence_not_recorded"},
                "evidence_status": "RECONSTRUCTED_MISSING_UNPROVEN",
            }
            source = "daily_candidates.raw_json" if _json_has_value(candidate.get("raw_json")) else ""
        if value is None and field == "ranking_basis":
            if _json_has_value(candidate.get("rank")) or _json_has_value(candidate.get("final_score")):
                value = {"rank": candidate.get("rank"), "final_score": candidate.get("final_score"), "basis_status": "RECORDED_RANK_SCORE_ONLY"}
                source = "daily_candidates.rank"
        if value is None:
            value = {} if field in ("ticket_reason", "factor_snapshot", "auxiliary_evidence_snapshot", "ranking_basis") else []
        missing = [] if _json_has_value(value) else [field]
        confidence = "HIGH" if source in ("daily_candidates.raw_json", "daily_candidates." + field) else ("MEDIUM" if source else "LOW")
        if isinstance(value, dict) and source:
            value.setdefault("reconstructed", True)
            value.setdefault("reconstruction_source", [source])
            value.setdefault("reconstruction_confidence", confidence)
            value.setdefault("missing_fields", missing)
        output[field] = value
        provenance[field] = {
            "reconstructed": bool(source and not _json_has_value(existing)),
            "reconstruction_source": [source] if source else [],
            "reconstruction_confidence": confidence,
            "missing_fields": missing,
        }
        if source and source not in sources_used:
            sources_used.append(source)

    future = dict(candidate.get("future_return_fields_placeholder") or {})
    for key in ("t1_return", "t2_return", "t3_return", "t5_return", "is_limit_up"):
        if key not in future:
            future[key] = return_row.get(key)
    output["future_return_fields_placeholder"] = future
    provenance["future_return_fields_placeholder"] = {
        "reconstructed": bool(return_row),
        "reconstruction_source": ["returns"] if return_row else [],
        "reconstruction_confidence": "HIGH" if return_row else "LOW",
        "missing_fields": [] if return_row else ["returns"],
    }
    output["reconstruction_provenance"] = provenance
    output["reconstruction_sources"] = sources_used
    return output


def _compact_key_fragment(value: Any, limit: int = 32) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    text = ''.join(ch if ch.isalnum() or ch in ('-', '_', '.', ':') else '-' for ch in text)
    while '--' in text:
        text = text.replace('--', '-')
    return text[:limit].strip('-_.:')


def _stable_directory_record_key(prefix: str, trade_date: date, record: Dict[str, Any], field_names: List[str]) -> str:
    fragments = [_compact_key_fragment(record.get(name)) for name in field_names]
    fragments = [fragment for fragment in fragments if fragment]
    payload = json.dumps(record, ensure_ascii=False, default=str, sort_keys=True)
    digest = hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]
    parts = [prefix, str(trade_date)]
    if fragments:
        parts.extend(fragments[:3])
    parts.append(digest)
    return ':'.join(parts)[:200]


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(sql_path: str = "scripts/xiaogu_db_init.sql") -> None:
    """Run init SQL to create tables."""
    with open(sql_path, "r") as f:
        sql = f.read()
    with engine.connect() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'scoring_config'
                      AND column_name = 'config_value'
                      AND data_type <> 'text'
                ) THEN
                    ALTER TABLE scoring_config
                        ALTER COLUMN config_value TYPE TEXT USING config_value::text;
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'scoring_config'
                      AND column_name = 'config_key'
                      AND character_maximum_length < 100
                ) THEN
                    ALTER TABLE scoring_config
                        ALTER COLUMN config_key TYPE VARCHAR(100);
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'scoring_config'
                ) THEN
                    ALTER TABLE scoring_config
                        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW(),
                        ADD COLUMN IF NOT EXISTS data_version VARCHAR(64);
                END IF;

            END $$;
        """))
        conn.execute(text(sql))
        conn.commit()


def create_production_run(
    trade_date: date,
    production_run_id: Optional[str] = None,
    *,
    scan_session_id: Optional[int] = None,
    run_mode: str = "LIVE_DAILY_PIPELINE",
    rule_version: str = "",
    runner_version: str = "xiaogu_forward_runner",
    scanner_version: str = "",
    schema_version: str = "",
    scoring_config_snapshot: Optional[Dict[str, Any]] = None,
    scoring_config_hash: str = "",
    input_payload_hash: str = "",
    db: Any | None = None,
) -> str:
    """Create or validate one immutable production run record."""
    run_id = str(production_run_id or uuid.uuid4())
    payload = {
        "production_run_id": run_id,
        "trade_date": trade_date,
        "scan_session_id": scan_session_id,
        "run_mode": run_mode,
        "rule_version": rule_version,
        "runner_version": runner_version,
        "scanner_version": scanner_version,
        "schema_version": schema_version,
        "scoring_config_snapshot": json.dumps(scoring_config_snapshot or {}, ensure_ascii=False, default=str),
        "scoring_config_hash": scoring_config_hash,
        "input_payload_hash": input_payload_hash,
    }
    existing_statement = text("""
        SELECT trade_date, scan_session_id, run_mode, rule_version, runner_version,
               scanner_version, schema_version, scoring_config_hash, input_payload_hash,
               status
        FROM production_runs
        WHERE production_run_id = :production_run_id
    """)
    insert_statement = text("""
        INSERT INTO production_runs (
            production_run_id, trade_date, scan_session_id, run_mode, rule_version, runner_version,
            scanner_version, schema_version, scoring_config_snapshot, scoring_config_hash,
            input_payload_hash, status, started_at
        ) VALUES (
            :production_run_id, :trade_date, :scan_session_id, :run_mode, :rule_version, :runner_version,
            :scanner_version, :schema_version, CAST(:scoring_config_snapshot AS jsonb),
            :scoring_config_hash, :input_payload_hash, 'RUNNING', NOW()
        )
    """)
    fill_scan_session_statement = text("""
        UPDATE production_runs
        SET scan_session_id = :scan_session_id,
            updated_at = NOW()
        WHERE production_run_id = :production_run_id
          AND scan_session_id IS NULL
          AND status NOT IN ('PASS', 'FAIL', 'FAILED_PERSISTENCE')
    """)

    def create_or_validate(active_db: Any) -> None:
        existing = active_db.execute(
            existing_statement,
            {"production_run_id": run_id},
        ).mappings().first()
        if not existing:
            active_db.execute(insert_statement, payload)
            return

        existing_values = dict(existing)
        immutable_fields = {
            "trade_date": trade_date,
            "run_mode": run_mode,
            "rule_version": rule_version,
            "runner_version": runner_version,
            "scanner_version": scanner_version,
            "schema_version": schema_version,
            "scoring_config_hash": scoring_config_hash,
            "input_payload_hash": input_payload_hash,
        }
        mismatches = [
            field
            for field, expected in immutable_fields.items()
            if str(existing_values.get(field) or "") != str(expected or "")
        ]
        if mismatches:
            raise ValueError(
                "PRODUCTION_RUN_IMMUTABLE_MISMATCH:" + ",".join(mismatches)
            )

        existing_status = str(existing_values.get("status") or "")
        existing_scan_session_id = existing_values.get("scan_session_id")
        if (
            existing_status not in {"PASS", "FAIL", "FAILED_PERSISTENCE"}
            and existing_scan_session_id is None
            and scan_session_id is not None
        ):
            active_db.execute(
                fill_scan_session_statement,
                {
                    "production_run_id": run_id,
                    "scan_session_id": scan_session_id,
                },
            )
        elif (
            existing_scan_session_id is not None
            and scan_session_id is not None
            and int(existing_scan_session_id) != int(scan_session_id)
        ):
            raise ValueError("PRODUCTION_RUN_IMMUTABLE_MISMATCH:scan_session_id")

    if db is None:
        with get_db() as active_db:
            create_or_validate(active_db)
    else:
        create_or_validate(db)
    return run_id


def update_production_run_step(
    production_run_id: str,
    step_name: str,
    status: str,
    *,
    required: bool = True,
    error_message: str = "",
    retry_command: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    db: Any | None = None,
) -> None:
    """Persist one run step without changing the formal ranking contract."""
    payload = {
        "production_run_id": production_run_id,
        "step_name": step_name,
        "status": status,
        "required": required,
        "error_message": error_message,
        "retry_command": retry_command,
        "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
    }
    statement = text("""
        INSERT INTO production_run_steps (
            production_run_id, step_name, status, required, started_at, completed_at,
            error_message, retry_command, metadata, updated_at
        ) VALUES (
            :production_run_id, :step_name, :status, :required,
            CASE WHEN :status IN ('RUNNING', 'PENDING') THEN NOW() ELSE NULL END,
            CASE WHEN :status IN ('PASS', 'FAIL', 'FAILED_PERSISTENCE', 'SKIPPED') THEN NOW() ELSE NULL END,
            :error_message, :retry_command, CAST(:metadata AS jsonb), NOW()
        )
        ON CONFLICT (production_run_id, step_name) DO UPDATE SET
            status = EXCLUDED.status,
            required = EXCLUDED.required,
            started_at = COALESCE(production_run_steps.started_at, EXCLUDED.started_at),
            completed_at = EXCLUDED.completed_at,
            error_message = EXCLUDED.error_message,
            retry_command = EXCLUDED.retry_command,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """)
    if db is None:
        with get_db() as active_db:
            active_db.execute(statement, payload)
    else:
        db.execute(statement, payload)


def update_production_run_status(
    production_run_id: str,
    status: str,
    *,
    error_message: str = "",
    retry_command: str = "",
    db: Any | None = None,
) -> None:
    """Record the terminal or retryable state for one production run."""
    statement = text("""
        UPDATE production_runs
        SET status = :status,
            error_message = :error_message,
            retry_command = :retry_command,
            completed_at = CASE
                WHEN :status IN ('PASS', 'FAILED_PERSISTENCE', 'FAIL') THEN NOW()
                ELSE completed_at
            END,
            updated_at = NOW()
        WHERE production_run_id = :production_run_id
          AND status NOT IN ('PASS', 'FAILED_PERSISTENCE', 'FAIL')
    """)
    payload = {
        "production_run_id": production_run_id,
        "status": status,
        "error_message": error_message,
        "retry_command": retry_command,
    }
    if db is None:
        with get_db() as active_db:
            active_db.execute(statement, payload)
    else:
        db.execute(statement, payload)


MODEL_REGISTRY_STATUSES = frozenset({
    "RESEARCH", "VALIDATING", "VALIDATED", "SHADOW",
    "PAPER_PRODUCTION", "PRODUCTION", "LIVE_READY", "RETIRED",
})
MODEL_ACCEPTANCE_REQUIRED_GATES = (
    "TARGET_COVERAGE",
    "LEAKAGE_AUDIT",
    "WALK_FORWARD_OOS",
    "TOP1_VS_RANDOM",
    "TOP1_VS_CURRENT_BASELINE",
    "REGIME_STABILITY",
    "DRAWDOWN_LIMIT",
)


def register_model(
    model_id: str,
    *,
    feature_version: str,
    label_version: str,
    status: str = "RESEARCH",
    training_start: Optional[date] = None,
    training_end: Optional[date] = None,
    validation_start: Optional[date] = None,
    validation_end: Optional[date] = None,
    oos_start: Optional[date] = None,
    oos_end: Optional[date] = None,
    universe_definition: str = "",
    model_type: str = "",
    parameters_hash: str = "",
    feature_hash: str = "",
    performance_summary: Optional[Dict[str, Any]] = None,
    acceptance_artifact: Optional[Dict[str, Any]] = None,
    db: Any | None = None,
) -> None:
    """Register one model without allowing implicit research promotion."""
    normalized_status = str(status or "RESEARCH").upper()
    if normalized_status not in MODEL_REGISTRY_STATUSES:
        raise ValueError("MODEL_REGISTRY_STATUS_INVALID")
    artifact = acceptance_artifact if isinstance(acceptance_artifact, dict) else {}
    if normalized_status in {"PAPER_PRODUCTION", "PRODUCTION", "LIVE_READY"}:
        missing_gates = [
            gate for gate in MODEL_ACCEPTANCE_REQUIRED_GATES
            if str(artifact.get(gate) or artifact.get(gate.lower()) or "").upper() != "PASS"
        ]
        if artifact.get("status") != "PASS" or missing_gates:
            raise ValueError(
                "MODEL_PRODUCTION_ACCEPTANCE_REQUIRED"
                + (":" + ",".join(missing_gates) if missing_gates else "")
            )
    if normalized_status == "LIVE_READY" and artifact.get("paper_production_status") != "PASS":
        raise ValueError("MODEL_PRODUCTION_ACCEPTANCE_REQUIRED")
    payload = {
        "model_id": str(model_id),
        "feature_version": feature_version,
        "label_version": label_version,
        "training_start": training_start,
        "training_end": training_end,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "oos_start": oos_start,
        "oos_end": oos_end,
        "universe_definition": universe_definition,
        "model_type": model_type,
        "parameters_hash": parameters_hash,
        "feature_hash": feature_hash,
        "performance_summary": json.dumps(performance_summary or {}, ensure_ascii=False, default=str),
        "acceptance_artifact": json.dumps(artifact, ensure_ascii=False, default=str),
        "status": normalized_status,
    }
    statement = text("""
        INSERT INTO model_registry (
            model_id, feature_version, label_version, training_start, training_end,
            validation_start, validation_end, oos_start, oos_end, universe_definition,
            model_type, parameters_hash, feature_hash, performance_summary,
            acceptance_artifact, status, created_at, updated_at
        ) VALUES (
            :model_id, :feature_version, :label_version, :training_start, :training_end,
            :validation_start, :validation_end, :oos_start, :oos_end, :universe_definition,
            :model_type, :parameters_hash, :feature_hash, CAST(:performance_summary AS jsonb),
            CAST(:acceptance_artifact AS jsonb), :status, NOW(), NOW()
        )
        ON CONFLICT (model_id) DO UPDATE SET
            feature_version = EXCLUDED.feature_version,
            label_version = EXCLUDED.label_version,
            training_start = EXCLUDED.training_start,
            training_end = EXCLUDED.training_end,
            validation_start = EXCLUDED.validation_start,
            validation_end = EXCLUDED.validation_end,
            oos_start = EXCLUDED.oos_start,
            oos_end = EXCLUDED.oos_end,
            universe_definition = EXCLUDED.universe_definition,
            model_type = EXCLUDED.model_type,
            parameters_hash = EXCLUDED.parameters_hash,
            feature_hash = EXCLUDED.feature_hash,
            performance_summary = EXCLUDED.performance_summary,
            acceptance_artifact = EXCLUDED.acceptance_artifact,
            status = EXCLUDED.status,
            updated_at = NOW()
    """)
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        active_db.execute(statement, payload)


def record_alpha_health(
    health_date: date,
    *,
    model_id: Optional[str],
    status: str = "HEALTHY",
    kill_switch: bool = False,
    kill_switch_reason: str = "",
    rolling_win_rate: Optional[float] = None,
    rolling_expectancy: Optional[float] = None,
    rolling_profit_factor: Optional[float] = None,
    calibration_error: Optional[float] = None,
    feature_drift: Optional[float] = None,
    regime_drift: Optional[float] = None,
    evidence: Optional[Dict[str, Any]] = None,
    db: Any | None = None,
) -> None:
    """Persist one health observation; a kill switch only blocks the main chain."""
    payload = {
        "model_id": model_id,
        "health_date": health_date,
        "status": str(status or "HEALTHY").upper(),
        "kill_switch": bool(kill_switch),
        "kill_switch_reason": str(kill_switch_reason or ""),
        "rolling_win_rate": rolling_win_rate,
        "rolling_expectancy": rolling_expectancy,
        "rolling_profit_factor": rolling_profit_factor,
        "calibration_error": calibration_error,
        "feature_drift": feature_drift,
        "regime_drift": regime_drift,
        "evidence": json.dumps(evidence or {}, ensure_ascii=False, default=str),
    }
    statement = text("""
        INSERT INTO production_alpha_health (
            model_id, health_date, rolling_win_rate, rolling_expectancy,
            rolling_profit_factor, calibration_error, feature_drift, regime_drift,
            status, kill_switch, kill_switch_reason, evidence, created_at
        ) VALUES (
            :model_id, :health_date, :rolling_win_rate, :rolling_expectancy,
            :rolling_profit_factor, :calibration_error, :feature_drift, :regime_drift,
            :status, :kill_switch, :kill_switch_reason, CAST(:evidence AS jsonb), NOW()
        )
        ON CONFLICT (model_id, health_date) DO UPDATE SET
            rolling_win_rate = EXCLUDED.rolling_win_rate,
            rolling_expectancy = EXCLUDED.rolling_expectancy,
            rolling_profit_factor = EXCLUDED.rolling_profit_factor,
            calibration_error = EXCLUDED.calibration_error,
            feature_drift = EXCLUDED.feature_drift,
            regime_drift = EXCLUDED.regime_drift,
            status = EXCLUDED.status,
            kill_switch = EXCLUDED.kill_switch,
            kill_switch_reason = EXCLUDED.kill_switch_reason,
            evidence = EXCLUDED.evidence
    """)
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        active_db.execute(statement, payload)


def alpha_kill_switch_active(
    *,
    model_id: Optional[str] = None,
    db: Any | None = None,
) -> Dict[str, Any]:
    """Read the latest health state used by the sole production gate."""
    statement = text("""
        SELECT model_id, health_date, status, kill_switch, kill_switch_reason
        FROM production_alpha_health
        WHERE (:model_id IS NULL OR model_id = :model_id)
        ORDER BY health_date DESC, id DESC
        LIMIT 1
    """)
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        row = active_db.execute(statement, {"model_id": model_id}).mappings().first()
    if not row:
        return {"active": False, "status": "NO_HEALTH_RECORD", "reason": ""}
    values = dict(row)
    active = bool(values.get("kill_switch")) or str(values.get("status") or "").upper() in {
        "KILLED", "PAPER_ONLY", "BLOCKED",
    }
    return {
        "active": active,
        "status": str(values.get("status") or ""),
        "reason": str(values.get("kill_switch_reason") or ""),
        "model_id": values.get("model_id"),
        "health_date": str(values.get("health_date") or ""),
    }


def set_active_production_run(
    trade_date: date,
    production_run_id: str,
    *,
    candidate_snapshot_id: str = "",
    active_pick_id: Optional[int] = None,
    db: Any | None = None,
) -> None:
    """Publish one fully completed production run as the day's active read."""
    payload = {
        "trade_date": trade_date,
        "production_run_id": production_run_id,
        "candidate_snapshot_id": candidate_snapshot_id or None,
        "active_pick_id": active_pick_id,
    }
    run_statement = text("""
        SELECT trade_date, status
        FROM production_runs
        WHERE production_run_id = :production_run_id
    """)
    required_steps_statement = text("""
        SELECT step_name, status
        FROM production_run_steps
        WHERE production_run_id = :production_run_id
          AND required = TRUE
    """)
    active_pick_statement = text("""
        SELECT production_run_id
        FROM picks
        WHERE id = :active_pick_id
    """)
    candidate_snapshot_statement = text("""
        SELECT 1
        FROM daily_candidates
        WHERE production_run_id = :production_run_id
          AND candidate_snapshot_id = :candidate_snapshot_id
        LIMIT 1
    """)
    statement = text("""
        INSERT INTO production_run_active (
            trade_date, production_run_id, candidate_snapshot_id, active_pick_id, updated_at
        ) VALUES (:trade_date, :production_run_id, :candidate_snapshot_id, :active_pick_id, NOW())
        ON CONFLICT (trade_date) DO UPDATE SET
            production_run_id = EXCLUDED.production_run_id,
            candidate_snapshot_id = EXCLUDED.candidate_snapshot_id,
            active_pick_id = EXCLUDED.active_pick_id,
            updated_at = NOW()
    """)

    def validate_and_publish(active_db: Any) -> None:
        run = active_db.execute(
            run_statement,
            {"production_run_id": production_run_id},
        ).mappings().first()
        if not run:
            raise ValueError("PRODUCTION_RUN_NOT_FOUND")
        run_values = dict(run)
        if str(run_values.get("trade_date")) != str(trade_date):
            raise ValueError("PRODUCTION_RUN_TRADE_DATE_MISMATCH")
        if str(run_values.get("status") or "") != "PASS":
            raise ValueError("PRODUCTION_RUN_NOT_READY_FOR_ACTIVE")

        required_steps = active_db.execute(
            required_steps_statement,
            {"production_run_id": production_run_id},
        ).mappings().all()
        if not required_steps or any(
            str(step.get("status") or "") != "PASS"
            for step in required_steps
        ):
            raise ValueError("PRODUCTION_RUN_NOT_READY_FOR_ACTIVE")

        if active_pick_id is not None:
            pick = active_db.execute(
                active_pick_statement,
                {"active_pick_id": active_pick_id},
            ).mappings().first()
            if not pick or str(dict(pick).get("production_run_id") or "") != production_run_id:
                raise ValueError("PRODUCTION_RUN_ACTIVE_PICK_MISMATCH")

        if candidate_snapshot_id:
            candidate = active_db.execute(
                candidate_snapshot_statement,
                {
                    "production_run_id": production_run_id,
                    "candidate_snapshot_id": candidate_snapshot_id,
                },
            ).first()
            if not candidate:
                raise ValueError("PRODUCTION_RUN_CANDIDATE_SNAPSHOT_MISMATCH")

        active_db.execute(statement, payload)

    if db is None:
        with get_db() as active_db:
            validate_and_publish(active_db)
    else:
        validate_and_publish(db)


def fetch_active_production_run(trade_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
    where = "" if trade_date is None else "WHERE pra.trade_date = :trade_date"
    params = {} if trade_date is None else {"trade_date": trade_date}
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT pra.trade_date, pra.production_run_id, pra.candidate_snapshot_id, pra.active_pick_id,
                   pr.status, pr.run_mode, pr.rule_version, pr.runner_version, pr.scanner_version,
                   pr.schema_version, pr.scoring_config_hash, pr.input_payload_hash,
                   pr.created_at, pr.started_at, pr.completed_at, pr.error_message, pr.retry_command
            FROM production_run_active pra
            JOIN production_runs pr ON pr.production_run_id = pra.production_run_id
            {where}
            ORDER BY pra.updated_at DESC
            LIMIT 1
        """), params).mappings().first()
    return dict(row) if row else None


def fetch_production_run(production_run_id: str) -> Optional[Dict[str, Any]]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM production_runs WHERE production_run_id = :production_run_id"),
            {"production_run_id": production_run_id},
        ).mappings().first()
    return dict(row) if row else None


def fetch_production_run_steps(production_run_id: str) -> List[Dict[str, Any]]:
    """Return the authoritative step ledger for one production run."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT production_run_id, step_name, status, required, started_at, completed_at,
                       error_message, retry_command, metadata, updated_at
                FROM production_run_steps
                WHERE production_run_id = :production_run_id
                ORDER BY step_name
            """),
            {"production_run_id": production_run_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def _normalize_scoring_config_rows(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    config = dict(SCORING_CONFIG_DEFAULTS)
    for row in rows:
        key = str(row.get("config_key") or "").strip()
        if not key:
            continue
        value = row.get("config_value")
        config[key] = "" if value is None else str(value)
    return config


def _load_scoring_config_snapshot() -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT config_key, config_value FROM scoring_config")
        ).mappings().all()
    return {
        "config": _normalize_scoring_config_rows(rows),
        "loaded": True,
        "source": "db",
        "error": "",
    }


def get_scoring_config_snapshot(refresh: bool = False) -> Dict[str, Any]:
    global _SCORING_CONFIG_CACHE
    if refresh or _SCORING_CONFIG_CACHE is None:
        try:
            snapshot = _load_scoring_config_snapshot()
        except Exception as exc:
            snapshot = {
                "config": dict(SCORING_CONFIG_DEFAULTS),
                "loaded": False,
                "source": "defaults",
                "error": repr(exc),
            }
        _SCORING_CONFIG_CACHE = snapshot
    return {
        "config": dict(_SCORING_CONFIG_CACHE["config"]),
        "loaded": bool(_SCORING_CONFIG_CACHE.get("loaded")),
        "source": str(_SCORING_CONFIG_CACHE.get("source") or "defaults"),
        "error": str(_SCORING_CONFIG_CACHE.get("error") or ""),
    }


def clear_scoring_config_cache() -> None:
    global _SCORING_CONFIG_CACHE
    _SCORING_CONFIG_CACHE = None


def insert_pick(
    trade_date: date,
    symbol: str,
    decision: str,
    final_score: Optional[float],
    blockers: List[str],
    features: Dict[str, Any],
    source_layers: List[str],
    rule_version: str,
    scan_dir: str = "",
    dry_run: bool = True,
    stock_name: str = "",
    rank: Optional[int] = None,
    structured_score: Optional[float] = None,
    ranking_basis: Optional[Dict[str, Any]] = None,
    ticket_reason: Optional[Dict[str, Any]] = None,
    selection_reason: Optional[Dict[str, Any]] = None,
    paper_pick_eligibility: Optional[Dict[str, Any]] = None,
    official_target_exclusion_reasons: Optional[List[str]] = None,
    risk_flags: Optional[List[str]] = None,
    auxiliary_evidence_status: str = "",
    information_coverage_audit_snapshot: Optional[Dict[str, Any]] = None,
    source_summary_path: str = "",
    production_run_id: Optional[str] = None,
    formal_rank_snapshot_id: str = "",
    formal_rank_snapshot_version: str = "",
    scoring_config_hash: str = "",
    db: Any | None = None,
) -> int:
    """Insert a pick; production rows are bound to one immutable run."""
    production = bool(production_run_id)
    update = """DO UPDATE SET
        final_score = EXCLUDED.final_score, blockers = EXCLUDED.blockers,
        features = EXCLUDED.features, source_layers = EXCLUDED.source_layers,
        rule_version = EXCLUDED.rule_version, scan_dir = EXCLUDED.scan_dir,
        dry_run = EXCLUDED.dry_run, stock_name = EXCLUDED.stock_name, rank = EXCLUDED.rank,
        structured_score = EXCLUDED.structured_score, ranking_basis = EXCLUDED.ranking_basis,
        ticket_reason = EXCLUDED.ticket_reason, selection_reason = EXCLUDED.selection_reason,
        paper_pick_eligibility = EXCLUDED.paper_pick_eligibility,
        official_target_exclusion_reasons = EXCLUDED.official_target_exclusion_reasons,
        risk_flags = EXCLUDED.risk_flags, auxiliary_evidence_status = EXCLUDED.auxiliary_evidence_status,
        information_coverage_audit_snapshot = EXCLUDED.information_coverage_audit_snapshot,
        source_summary_path = EXCLUDED.source_summary_path,
        formal_rank_snapshot_id = EXCLUDED.formal_rank_snapshot_id,
        formal_rank_snapshot_version = EXCLUDED.formal_rank_snapshot_version,
        scoring_config_hash = EXCLUDED.scoring_config_hash, updated_at = NOW()"""
    if production:
        statement = text(f"""
            INSERT INTO picks (
                trade_date, symbol, decision, final_score, blockers, features, source_layers,
                rule_version, scan_dir, dry_run, stock_name, rank, structured_score,
                ranking_basis, ticket_reason, selection_reason, paper_pick_eligibility,
                official_target_exclusion_reasons, risk_flags, auxiliary_evidence_status,
                information_coverage_audit_snapshot, source_summary_path, production_run_id,
                formal_rank_snapshot_id, formal_rank_snapshot_version, scoring_config_hash
            ) VALUES (
                :trade_date, :symbol, :decision, :final_score, CAST(:blockers AS jsonb),
                CAST(:features AS jsonb), CAST(:source_layers AS jsonb), :rule_version, :scan_dir, :dry_run,
                :stock_name, :rank, :structured_score, CAST(:ranking_basis AS jsonb), CAST(:ticket_reason AS jsonb),
                CAST(:selection_reason AS jsonb), CAST(:paper_pick_eligibility AS jsonb),
                CAST(:official_target_exclusion_reasons AS jsonb), CAST(:risk_flags AS jsonb),
                :auxiliary_evidence_status, CAST(:information_coverage_audit_snapshot AS jsonb),
                :source_summary_path, :production_run_id, :formal_rank_snapshot_id,
                :formal_rank_snapshot_version, :scoring_config_hash
            ) ON CONFLICT (production_run_id, symbol, decision) WHERE production_run_id IS NOT NULL
            {update} RETURNING id
        """)
    else:
        legacy_update = update.replace(
            """        source_summary_path = EXCLUDED.source_summary_path,
        formal_rank_snapshot_id = EXCLUDED.formal_rank_snapshot_id,
        formal_rank_snapshot_version = EXCLUDED.formal_rank_snapshot_version,
        scoring_config_hash = EXCLUDED.scoring_config_hash, updated_at = NOW()""",
            """        source_summary_path = EXCLUDED.source_summary_path,
        updated_at = NOW()""",
        )
        statement = text(f"""
            INSERT INTO picks (
                trade_date, symbol, decision, final_score, blockers, features, source_layers,
                rule_version, scan_dir, dry_run, stock_name, rank, structured_score,
                ranking_basis, ticket_reason, selection_reason, paper_pick_eligibility,
                official_target_exclusion_reasons, risk_flags, auxiliary_evidence_status,
                information_coverage_audit_snapshot, source_summary_path
            ) VALUES (
                :trade_date, :symbol, :decision, :final_score, CAST(:blockers AS jsonb),
                CAST(:features AS jsonb), CAST(:source_layers AS jsonb), :rule_version, :scan_dir, :dry_run,
                :stock_name, :rank, :structured_score, CAST(:ranking_basis AS jsonb), CAST(:ticket_reason AS jsonb),
                CAST(:selection_reason AS jsonb), CAST(:paper_pick_eligibility AS jsonb),
                CAST(:official_target_exclusion_reasons AS jsonb), CAST(:risk_flags AS jsonb),
                :auxiliary_evidence_status, CAST(:information_coverage_audit_snapshot AS jsonb),
                :source_summary_path
            ) ON CONFLICT (trade_date, symbol, decision) WHERE production_run_id IS NULL
            {legacy_update} RETURNING id
        """)
        legacy_compat_statement = text(
            str(statement).replace(
                "ON CONFLICT (trade_date, symbol, decision) WHERE production_run_id IS NULL",
                "ON CONFLICT (trade_date, symbol, decision)",
            )
        )
    params = {
        "trade_date": trade_date, "symbol": symbol, "decision": decision, "final_score": final_score,
        "blockers": json.dumps(blockers, ensure_ascii=False), "features": json.dumps(features, ensure_ascii=False, default=str),
        "source_layers": json.dumps(source_layers, ensure_ascii=False), "rule_version": rule_version,
        "scan_dir": scan_dir, "dry_run": dry_run, "stock_name": stock_name, "rank": rank,
        "structured_score": structured_score, "ranking_basis": json.dumps(ranking_basis or {}, ensure_ascii=False, default=str),
        "ticket_reason": json.dumps(ticket_reason or {}, ensure_ascii=False, default=str),
        "selection_reason": json.dumps(selection_reason or {}, ensure_ascii=False, default=str),
        "paper_pick_eligibility": json.dumps(paper_pick_eligibility or {}, ensure_ascii=False, default=str),
        "official_target_exclusion_reasons": json.dumps(official_target_exclusion_reasons or [], ensure_ascii=False, default=str),
        "risk_flags": json.dumps(risk_flags or [], ensure_ascii=False, default=str),
        "auxiliary_evidence_status": auxiliary_evidence_status,
        "information_coverage_audit_snapshot": json.dumps(information_coverage_audit_snapshot or {}, ensure_ascii=False, default=str),
        "source_summary_path": source_summary_path, "production_run_id": production_run_id,
        "formal_rank_snapshot_id": formal_rank_snapshot_id or None,
        "formal_rank_snapshot_version": formal_rank_snapshot_version or None,
        "scoring_config_hash": scoring_config_hash or None,
    }
    context = get_db() if db is None else nullcontext(db)
    try:
        with context as active_db:
            row = active_db.execute(statement, params).fetchone()
            return int(row[0]) if row else -1
    except Exception as exc:
        # A pre-lineage database may still be used by dry-run/replay callers.
        # Production writes never fall back because that would hide a migration
        # failure behind an unbound pick.
        if production or db is not None or 'production_run_id' not in str(exc):
            raise
        with get_db() as legacy_db:
            row = legacy_db.execute(legacy_compat_statement, params).fetchone()
            return int(row[0]) if row else -1


def has_returns_for_trade_date(trade_date: date) -> bool:
    """Corrections are forbidden once T+1 return evidence exists for the day."""
    with engine.connect() as conn:
        return bool(conn.execute(
            text("""
                SELECT EXISTS(
                    SELECT 1
                    FROM returns
                    WHERE trade_date = :trade_date
                )
            """),
            {"trade_date": trade_date},
        ).scalar())


def prune_daily_candidates_to_symbols(trade_date: date, symbols: List[str]) -> int:
    """Retain stale same-day candidates; historical DB rows are never deleted."""
    # Kept as a compatibility boundary for old callers. A correction may archive
    # the prior snapshot and upsert the replacement, but it must not remove rows.
    _ = trade_date, symbols
    return 0


def supersede_active_picks_for_correction(
    trade_date: date,
    *,
    correction_of: str,
    replacement_symbol: str,
    replacement_decision: str,
    reason: str,
) -> int:
    """Retain prior DB pick rows for audit while hiding replaced decisions by default."""
    metadata = {
        "superseded": True,
        "superseded_by": correction_of,
        "superseded_reason": reason,
        "superseded_at": datetime.now(timezone.utc).isoformat(),
    }
    with get_db() as db:
        has_returns = db.execute(
            text("""
                SELECT EXISTS(
                    SELECT 1
                    FROM returns
                    WHERE trade_date = :trade_date
                )
            """),
            {"trade_date": trade_date},
        ).scalar()
        if has_returns:
            raise RuntimeError("cannot supersede pick after returns exist")
        result = db.execute(
            text("""
                UPDATE picks
                SET features = COALESCE(features, CAST('{}' AS jsonb))
                    || CAST(:metadata AS jsonb),
                    updated_at = NOW()
                WHERE trade_date = :trade_date
                  AND COALESCE(features ->> 'superseded', 'false') <> 'true'
                  AND COALESCE(features ->> 'user_locked_official', 'false') <> 'true'
                  AND NOT (symbol = :replacement_symbol AND decision = :replacement_decision)
            """),
            {
                "trade_date": trade_date,
                "replacement_symbol": replacement_symbol,
                "replacement_decision": replacement_decision,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )
    return int(result.rowcount or 0)


def fetch_user_locked_official_pick(trade_date: date) -> Optional[Dict[str, Any]]:
    """Return active user-locked formal PAPER_PICK for a date, if any."""
    with get_db() as db:
        row = db.execute(
            text("""
                SELECT id, symbol, stock_name, decision, final_score, features
                FROM picks
                WHERE trade_date = :trade_date
                  AND decision = 'PAPER_PICK'
                  AND COALESCE(features ->> 'user_locked_official', 'false') = 'true'
                  AND COALESCE(features ->> 'superseded', 'false') <> 'true'
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
            """),
            {"trade_date": trade_date},
        ).mappings().first()
    return dict(row) if row else None


def mark_pick_active_correction(
    trade_date: date,
    *,
    symbol: str,
    decision: str,
    correction_of: str,
) -> int:
    """Mark the replacement pick as the active materialized correction result."""
    metadata = {
        "correction_record_type": "CORRECTION",
        "correction_of": correction_of,
        "superseded": False,
        "active_correction": True,
        "corrected_at": datetime.now(timezone.utc).isoformat(),
    }
    with get_db() as db:
        result = db.execute(
            text("""
                UPDATE picks
                SET features = (
                        COALESCE(features, CAST('{}' AS jsonb))
                        - 'superseded'
                        - 'superseded_by'
                        - 'superseded_reason'
                        - 'superseded_at'
                    ) || CAST(:metadata AS jsonb),
                    updated_at = NOW()
                WHERE trade_date = :trade_date
                  AND symbol = :symbol
                  AND decision = :decision
            """),
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "decision": decision,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )
    return int(result.rowcount or 0)


def upsert_daily_candidate(
    trade_date: date,
    symbol: str,
    stock_name: str,
    rank: Optional[int],
    final_score: Optional[float],
    decision: str,
    is_official_pick: bool,
    open_price: Optional[float],
    close_price: Optional[float],
    high_price: Optional[float],
    low_price: Optional[float],
    volume: Optional[int],
    amount: Optional[float],
    pct_chg: Optional[float],
    turnover_rate: Optional[float],
    signal_pct: Optional[float],
    close_position_score: Optional[float],
    fund_flow_momentum: Optional[float],
    sector_catalyst_score: Optional[float],
    early_opportunity_score: Optional[float],
    topic_propagation_score: Optional[float],
    market_regime: str,
    sentiment_catalyst: str,
    theme_catalyst: str,
    news_catalyst: str,
    positive_catalyst: str,
    selection_reason: str,
    selection_outcome: str = '',
    selection_outcome_reason: str = '',
    blockers: Optional[List[str]] = None,
    hard_gate_status: Optional[Dict[str, Any]] = None,
    eligibility_snapshot: Optional[Dict[str, Any]] = None,
    selection_diagnostics: Optional[Dict[str, Any]] = None,
    source_layers: Optional[List[str]] = None,
    candidate_features: Optional[Dict[str, Any]] = None,
    raw_json: Optional[Dict[str, Any]] = None,
    candidate_entry_reason: Optional[List[str]] = None,
    ticket_reason: Optional[Dict[str, Any]] = None,
    not_selected_reason: Optional[List[str]] = None,
    factor_snapshot: Optional[Dict[str, Any]] = None,
    auxiliary_evidence_snapshot: Optional[Dict[str, Any]] = None,
    ranking_basis: Optional[Dict[str, Any]] = None,
    postmortem_snapshot: Optional[Dict[str, Any]] = None,
    future_return_fields_placeholder: Optional[Dict[str, Any]] = None,
    cohort: str = "",
    cohort_quality: str = "",
    cohort_status_flags: Optional[List[str]] = None,
    reconstruction_provenance: Optional[Dict[str, Any]] = None,
    production_run_id: Optional[str] = None,
    candidate_snapshot_id: Optional[str] = None,
    db: Any | None = None,
) -> None:
    blockers = list(blockers or [])
    hard_gate_status = dict(hard_gate_status or {})
    eligibility_snapshot = dict(eligibility_snapshot or {})
    selection_diagnostics = dict(selection_diagnostics or {})
    source_layers = list(source_layers or [])
    candidate_features = dict(candidate_features or {})
    raw_json = dict(raw_json or {})
    candidate_entry_reason = list(candidate_entry_reason or []) if isinstance(candidate_entry_reason, (list, tuple)) else ([str(candidate_entry_reason)] if candidate_entry_reason else [])
    ticket_reason = dict(ticket_reason or {}) if isinstance(ticket_reason, dict) else ({"text": str(ticket_reason)} if ticket_reason else {})
    not_selected_reason = list(not_selected_reason or []) if isinstance(not_selected_reason, (list, tuple)) else ([str(not_selected_reason)] if not_selected_reason else [])
    factor_snapshot = dict(factor_snapshot or {})
    auxiliary_evidence_snapshot = dict(auxiliary_evidence_snapshot or {})
    ranking_basis = dict(ranking_basis or {})
    postmortem_snapshot = dict(postmortem_snapshot or {})
    future_return_fields_placeholder = dict(future_return_fields_placeholder or {})
    cohort_status_flags = list(cohort_status_flags or [])
    reconstruction_provenance = dict(reconstruction_provenance or {})
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        if production_run_id:
            active_db.execute(
                text("""
                    INSERT INTO daily_candidates (
                        trade_date, symbol, stock_name, rank, final_score, is_official_pick, decision,
                        production_run_id, candidate_snapshot_id,
                        open_price, close_price, high_price, low_price, volume, amount, pct_chg, turnover_rate,
                        signal_pct, close_position_score, fund_flow_momentum, sector_catalyst_score,
                        early_opportunity_score, topic_propagation_score, market_regime, sentiment_catalyst,
                        theme_catalyst, news_catalyst, positive_catalyst, selection_reason,
                        selection_outcome, selection_outcome_reason, blockers, hard_gate_status,
                        eligibility_snapshot, selection_diagnostics, source_layers, candidate_features, raw_json,
                        candidate_entry_reason, ticket_reason, not_selected_reason, factor_snapshot,
                        auxiliary_evidence_snapshot, ranking_basis, postmortem_snapshot, future_return_fields_placeholder,
                        cohort, cohort_quality, cohort_status_flags, reconstruction_provenance
                    ) VALUES (
                        :trade_date, :symbol, :stock_name, :rank, :final_score, :is_official_pick, :decision,
                        :production_run_id, :candidate_snapshot_id,
                        :open_price, :close_price, :high_price, :low_price, :volume, :amount, :pct_chg, :turnover_rate,
                        :signal_pct, :close_position_score, :fund_flow_momentum, :sector_catalyst_score,
                        :early_opportunity_score, :topic_propagation_score, :market_regime, :sentiment_catalyst,
                        :theme_catalyst, :news_catalyst, :positive_catalyst, :selection_reason,
                        :selection_outcome, :selection_outcome_reason, CAST(:blockers AS jsonb), CAST(:hard_gate_status AS jsonb),
                        CAST(:eligibility_snapshot AS jsonb), CAST(:selection_diagnostics AS jsonb), CAST(:source_layers AS jsonb),
                        CAST(:candidate_features AS jsonb), CAST(:raw_json AS jsonb), CAST(:candidate_entry_reason AS jsonb),
                        CAST(:ticket_reason AS jsonb), CAST(:not_selected_reason AS jsonb), CAST(:factor_snapshot AS jsonb),
                        CAST(:auxiliary_evidence_snapshot AS jsonb), CAST(:ranking_basis AS jsonb), CAST(:postmortem_snapshot AS jsonb),
                        CAST(:future_return_fields_placeholder AS jsonb), :cohort, :cohort_quality,
                        CAST(:cohort_status_flags AS jsonb), CAST(:reconstruction_provenance AS jsonb)
                    )
                    ON CONFLICT (production_run_id, symbol) WHERE production_run_id IS NOT NULL DO UPDATE SET
                        stock_name = EXCLUDED.stock_name, rank = EXCLUDED.rank, final_score = EXCLUDED.final_score,
                        is_official_pick = EXCLUDED.is_official_pick, decision = EXCLUDED.decision,
                        candidate_features = EXCLUDED.candidate_features, raw_json = EXCLUDED.raw_json,
                        ranking_basis = EXCLUDED.ranking_basis, updated_at = NOW()
                """),
                {
                    **{
                        'trade_date': trade_date, 'symbol': symbol, 'stock_name': stock_name, 'rank': rank,
                        'final_score': final_score, 'is_official_pick': is_official_pick, 'decision': decision,
                        'production_run_id': production_run_id, 'candidate_snapshot_id': candidate_snapshot_id,
                        'open_price': open_price, 'close_price': close_price, 'high_price': high_price, 'low_price': low_price,
                        'volume': volume, 'amount': amount, 'pct_chg': pct_chg, 'turnover_rate': turnover_rate,
                        'signal_pct': signal_pct, 'close_position_score': close_position_score,
                        'fund_flow_momentum': fund_flow_momentum, 'sector_catalyst_score': sector_catalyst_score,
                        'early_opportunity_score': early_opportunity_score, 'topic_propagation_score': topic_propagation_score,
                        'market_regime': market_regime, 'sentiment_catalyst': sentiment_catalyst, 'theme_catalyst': theme_catalyst,
                        'news_catalyst': news_catalyst, 'positive_catalyst': positive_catalyst,
                        'selection_reason': selection_reason, 'selection_outcome': selection_outcome,
                        'selection_outcome_reason': selection_outcome_reason,
                        'blockers': json.dumps(blockers, ensure_ascii=False, default=str),
                        'hard_gate_status': json.dumps(hard_gate_status, ensure_ascii=False, default=str),
                        'eligibility_snapshot': json.dumps(eligibility_snapshot, ensure_ascii=False, default=str),
                        'selection_diagnostics': json.dumps(selection_diagnostics, ensure_ascii=False, default=str),
                        'source_layers': json.dumps(source_layers, ensure_ascii=False, default=str),
                        'candidate_features': json.dumps(candidate_features, ensure_ascii=False, default=str),
                        'raw_json': json.dumps(raw_json, ensure_ascii=False, default=str),
                        'candidate_entry_reason': json.dumps(candidate_entry_reason, ensure_ascii=False, default=str),
                        'ticket_reason': json.dumps(ticket_reason, ensure_ascii=False, default=str),
                        'not_selected_reason': json.dumps(not_selected_reason, ensure_ascii=False, default=str),
                        'factor_snapshot': json.dumps(factor_snapshot, ensure_ascii=False, default=str),
                        'auxiliary_evidence_snapshot': json.dumps(auxiliary_evidence_snapshot, ensure_ascii=False, default=str),
                        'ranking_basis': json.dumps(ranking_basis, ensure_ascii=False, default=str),
                        'postmortem_snapshot': json.dumps(postmortem_snapshot, ensure_ascii=False, default=str),
                        'future_return_fields_placeholder': json.dumps(future_return_fields_placeholder, ensure_ascii=False, default=str),
                        'cohort': cohort, 'cohort_quality': cohort_quality,
                        'cohort_status_flags': json.dumps(cohort_status_flags, ensure_ascii=False, default=str),
                        'reconstruction_provenance': json.dumps(reconstruction_provenance, ensure_ascii=False, default=str),
                    }
                },
            )
            return
        active_db.execute(
            text("""
                INSERT INTO daily_candidates (
                    trade_date, symbol, stock_name, rank, final_score, is_official_pick, decision,
                    open_price, close_price, high_price, low_price, volume, amount, pct_chg, turnover_rate,
                    signal_pct, close_position_score, fund_flow_momentum, sector_catalyst_score,
                    early_opportunity_score, topic_propagation_score, market_regime, sentiment_catalyst,
                    theme_catalyst, news_catalyst, positive_catalyst, selection_reason,
                    selection_outcome, selection_outcome_reason, blockers, hard_gate_status,
                    eligibility_snapshot, selection_diagnostics, source_layers, candidate_features, raw_json,
                    candidate_entry_reason, ticket_reason, not_selected_reason, factor_snapshot,
                    auxiliary_evidence_snapshot, ranking_basis, postmortem_snapshot, future_return_fields_placeholder,
                    cohort, cohort_quality, cohort_status_flags, reconstruction_provenance
                ) VALUES (
                    :trade_date, :symbol, :stock_name, :rank, :final_score, :is_official_pick, :decision,
                    :open_price, :close_price, :high_price, :low_price, :volume, :amount, :pct_chg, :turnover_rate,
                    :signal_pct, :close_position_score, :fund_flow_momentum, :sector_catalyst_score,
                    :early_opportunity_score, :topic_propagation_score, :market_regime, :sentiment_catalyst,
                    :theme_catalyst, :news_catalyst, :positive_catalyst, :selection_reason,
                    :selection_outcome, :selection_outcome_reason, CAST(:blockers AS jsonb), CAST(:hard_gate_status AS jsonb),
                    CAST(:eligibility_snapshot AS jsonb), CAST(:selection_diagnostics AS jsonb), CAST(:source_layers AS jsonb),
                    CAST(:candidate_features AS jsonb), CAST(:raw_json AS jsonb),
                    CAST(:candidate_entry_reason AS jsonb), CAST(:ticket_reason AS jsonb),
                    CAST(:not_selected_reason AS jsonb), CAST(:factor_snapshot AS jsonb),
                    CAST(:auxiliary_evidence_snapshot AS jsonb), CAST(:ranking_basis AS jsonb),
                    CAST(:postmortem_snapshot AS jsonb), CAST(:future_return_fields_placeholder AS jsonb),
                    :cohort, :cohort_quality, CAST(:cohort_status_flags AS jsonb), CAST(:reconstruction_provenance AS jsonb)
                )
                ON CONFLICT (trade_date, symbol) WHERE production_run_id IS NULL DO UPDATE SET
                    stock_name = EXCLUDED.stock_name,
                    rank = EXCLUDED.rank,
                    final_score = EXCLUDED.final_score,
                    is_official_pick = EXCLUDED.is_official_pick,
                    decision = EXCLUDED.decision,
                    open_price = EXCLUDED.open_price,
                    close_price = EXCLUDED.close_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    volume = EXCLUDED.volume,
                    amount = EXCLUDED.amount,
                    pct_chg = EXCLUDED.pct_chg,
                    turnover_rate = EXCLUDED.turnover_rate,
                    signal_pct = EXCLUDED.signal_pct,
                    close_position_score = EXCLUDED.close_position_score,
                    fund_flow_momentum = EXCLUDED.fund_flow_momentum,
                    sector_catalyst_score = EXCLUDED.sector_catalyst_score,
                    early_opportunity_score = EXCLUDED.early_opportunity_score,
                    topic_propagation_score = EXCLUDED.topic_propagation_score,
                    market_regime = EXCLUDED.market_regime,
                    sentiment_catalyst = EXCLUDED.sentiment_catalyst,
                    theme_catalyst = EXCLUDED.theme_catalyst,
                    news_catalyst = EXCLUDED.news_catalyst,
                    positive_catalyst = EXCLUDED.positive_catalyst,
                    selection_reason = EXCLUDED.selection_reason,
                    selection_outcome = EXCLUDED.selection_outcome,
                    selection_outcome_reason = EXCLUDED.selection_outcome_reason,
                    blockers = EXCLUDED.blockers,
                    hard_gate_status = EXCLUDED.hard_gate_status,
                    eligibility_snapshot = EXCLUDED.eligibility_snapshot,
                    selection_diagnostics = EXCLUDED.selection_diagnostics,
                    source_layers = EXCLUDED.source_layers,
                    candidate_features = EXCLUDED.candidate_features,
                    raw_json = EXCLUDED.raw_json,
                    candidate_entry_reason = EXCLUDED.candidate_entry_reason,
                    ticket_reason = EXCLUDED.ticket_reason,
                    not_selected_reason = EXCLUDED.not_selected_reason,
                    factor_snapshot = EXCLUDED.factor_snapshot,
                    auxiliary_evidence_snapshot = EXCLUDED.auxiliary_evidence_snapshot,
                    ranking_basis = EXCLUDED.ranking_basis,
                    postmortem_snapshot = EXCLUDED.postmortem_snapshot,
                    future_return_fields_placeholder = EXCLUDED.future_return_fields_placeholder,
                    cohort = EXCLUDED.cohort,
                    cohort_quality = EXCLUDED.cohort_quality,
                    cohort_status_flags = EXCLUDED.cohort_status_flags,
                    reconstruction_provenance = EXCLUDED.reconstruction_provenance,
                    updated_at = NOW()
            """),
            {
                'trade_date': trade_date,
                'symbol': symbol,
                'stock_name': stock_name,
                'rank': rank,
                'final_score': final_score,
                'is_official_pick': is_official_pick,
                'decision': decision,
                'open_price': open_price,
                'close_price': close_price,
                'high_price': high_price,
                'low_price': low_price,
                'volume': volume,
                'amount': amount,
                'pct_chg': pct_chg,
                'turnover_rate': turnover_rate,
                'signal_pct': signal_pct,
                'close_position_score': close_position_score,
                'fund_flow_momentum': fund_flow_momentum,
                'sector_catalyst_score': sector_catalyst_score,
                'early_opportunity_score': early_opportunity_score,
                'topic_propagation_score': topic_propagation_score,
                'market_regime': market_regime,
                'sentiment_catalyst': sentiment_catalyst,
                'theme_catalyst': theme_catalyst,
                'news_catalyst': news_catalyst,
                'positive_catalyst': positive_catalyst,
                'selection_reason': selection_reason,
                'selection_outcome': selection_outcome,
                'selection_outcome_reason': selection_outcome_reason,
                'blockers': json.dumps(blockers, ensure_ascii=False, default=str),
                'hard_gate_status': json.dumps(hard_gate_status, ensure_ascii=False, default=str),
                'eligibility_snapshot': json.dumps(eligibility_snapshot, ensure_ascii=False, default=str),
                'selection_diagnostics': json.dumps(selection_diagnostics, ensure_ascii=False, default=str),
                'source_layers': json.dumps(source_layers, ensure_ascii=False, default=str),
                'candidate_features': json.dumps(candidate_features, ensure_ascii=False, default=str),
                'raw_json': json.dumps(raw_json, ensure_ascii=False, default=str),
                'candidate_entry_reason': json.dumps(candidate_entry_reason, ensure_ascii=False, default=str),
                'ticket_reason': json.dumps(ticket_reason, ensure_ascii=False, default=str),
                'not_selected_reason': json.dumps(not_selected_reason, ensure_ascii=False, default=str),
                'factor_snapshot': json.dumps(factor_snapshot, ensure_ascii=False, default=str),
                'auxiliary_evidence_snapshot': json.dumps(auxiliary_evidence_snapshot, ensure_ascii=False, default=str),
                'ranking_basis': json.dumps(ranking_basis, ensure_ascii=False, default=str),
                'postmortem_snapshot': json.dumps(postmortem_snapshot, ensure_ascii=False, default=str),
                'future_return_fields_placeholder': json.dumps(future_return_fields_placeholder, ensure_ascii=False, default=str),
                'cohort': cohort,
                'cohort_quality': cohort_quality,
                'cohort_status_flags': json.dumps(cohort_status_flags, ensure_ascii=False, default=str),
                'reconstruction_provenance': json.dumps(reconstruction_provenance, ensure_ascii=False, default=str),
            },
        )


def resolve_pick_id(
    trade_date: date,
    symbol: str,
    *,
    production_run_id: Optional[str] = None,
) -> Optional[int]:
    """Resolve a pick only within its production run or explicit legacy scope."""
    symbol_key = str(symbol or "").strip()
    if not symbol_key:
        return None
    with get_db() as db:
        result = db.execute(
            text(
                """
                SELECT id
                FROM picks
                WHERE trade_date = :trade_date
                  AND symbol = :symbol
                  AND (
                      (:production_run_id IS NULL AND production_run_id IS NULL)
                      OR production_run_id = :production_run_id
                  )
                ORDER BY
                    CASE WHEN UPPER(COALESCE(decision, '')) = 'PAPER_PICK' THEN 0 ELSE 1 END,
                    updated_at DESC NULLS LAST,
                    id DESC
                LIMIT 1
                """
            ),
            {
                "trade_date": trade_date,
                "symbol": symbol_key,
                "production_run_id": production_run_id,
            },
        )
        if result is None:
            return None
        row = result.fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def backfill_return_pick_ids() -> Dict[str, int]:
    """Link only legacy returns that predate production-run lineage."""
    with get_db() as db:
        result = db.execute(
            text(
                """
                UPDATE returns r
                SET pick_id = p.id
                FROM picks p
                WHERE r.pick_id IS NULL
                  AND r.production_run_id IS NULL
                  AND p.production_run_id IS NULL
                  AND p.trade_date = r.trade_date
                  AND p.symbol = r.symbol
                  AND p.id = (
                      SELECT p2.id
                      FROM picks p2
                      WHERE p2.trade_date = r.trade_date
                        AND p2.symbol = r.symbol
                      ORDER BY
                          CASE WHEN UPPER(COALESCE(p2.decision, '')) = 'PAPER_PICK' THEN 0 ELSE 1 END,
                          p2.updated_at DESC NULLS LAST,
                          p2.id DESC
                      LIMIT 1
                  )
                """
            )
        )
        linked = int(getattr(result, "rowcount", 0) or 0)
        remaining = db.execute(
            text("SELECT count(*) FROM returns WHERE pick_id IS NULL")
        ).scalar()
        total = db.execute(text("SELECT count(*) FROM returns")).scalar()
    return {
        "linked": linked,
        "null_pick_id_remaining": int(remaining or 0),
        "returns_total": int(total or 0),
    }


def upsert_return(
    trade_date: date,
    symbol: str,
    pick_id: Optional[int],
    t1_return: Optional[float] = None,
    t2_return: Optional[float] = None,
    t3_return: Optional[float] = None,
    t5_return: Optional[float] = None,
    t1_return_close: Optional[float] = None,
    t1_return_high: Optional[float] = None,
    is_limit_up: Optional[bool] = None,
    next_day_open_return: Optional[float] = None,
    next_day_high_return: Optional[float] = None,
    next_day_low_return: Optional[float] = None,
    next_day_gap_return: Optional[float] = None,
    next_day_drawdown: Optional[float] = None,
    high_to_close_retrace: Optional[float] = None,
    *,
    production_run_id: Optional[str] = None,
    candidate_snapshot_id: str = "",
    return_status: str = "",
    settlement_evidence: Optional[Dict[str, Any]] = None,
    t1_labels: Optional[Dict[str, Any]] = None,
    legacy_backfill: bool = False,
    db: Any | None = None,
) -> int:
    """Persist a T+1 settlement without crossing production-run boundaries."""
    normalized_symbol = str(symbol or "").strip().zfill(6)
    if not normalized_symbol:
        raise ValueError("RETURN_SYMBOL_REQUIRED")

    production = bool(production_run_id)
    if production and not candidate_snapshot_id:
        raise ValueError("PRODUCTION_RETURN_CANDIDATE_SNAPSHOT_REQUIRED")
    if not production and not legacy_backfill:
        raise ValueError("LEGACY_RETURN_BACKFILL_MUST_BE_EXPLICIT")

    evidence = settlement_evidence if isinstance(settlement_evidence, dict) else {}
    canonical_labels = (
        t1_labels
        if isinstance(t1_labels, dict) and t1_labels
        else evidence.get("t1_metrics")
        if isinstance(evidence.get("t1_metrics"), dict)
        else {}
    )
    t1_close_return = (
        t1_return
        if t1_return is not None
        else canonical_labels.get("t1_close_return")
    )
    payload = {
        "pick_id": pick_id,
        "trade_date": trade_date,
        "symbol": normalized_symbol,
        "t1_return": t1_close_return,
        "t2_return": t2_return,
        "t3_return": t3_return,
        "t5_return": t5_return,
        "t1_return_close": t1_return_close if t1_return_close is not None else canonical_labels.get("t1_close_return"),
        "t1_return_high": t1_return_high if t1_return_high is not None else canonical_labels.get("t1_high_return"),
        "next_day_open_return": next_day_open_return,
        "next_day_high_return": next_day_high_return if next_day_high_return is not None else canonical_labels.get("t1_high_return"),
        "next_day_low_return": next_day_low_return if next_day_low_return is not None else canonical_labels.get("t1_low_return"),
        "next_day_gap_return": next_day_gap_return,
        "next_day_drawdown": next_day_drawdown,
        "high_to_close_retrace": high_to_close_retrace,
        "t1_open_return": canonical_labels.get("t1_open_return"),
        "t1_high_return": canonical_labels.get("t1_high_return"),
        "t1_low_return": canonical_labels.get("t1_low_return"),
        "t1_close_return": canonical_labels.get("t1_close_return", t1_close_return),
        "t1_mfe": canonical_labels.get("t1_mfe"),
        "t1_mae": canonical_labels.get("t1_mae"),
        "t1_vwap_return": canonical_labels.get("t1_vwap_return"),
        "t1_gap_return": canonical_labels.get("t1_gap_return"),
        "t1_net_return": canonical_labels.get("t1_net_return"),
        "slippage": canonical_labels.get("slippage"),
        "commission": canonical_labels.get("commission"),
        "stamp_duty": canonical_labels.get("stamp_duty"),
        "transfer_fee": canonical_labels.get("transfer_fee"),
        "market_impact": canonical_labels.get("market_impact"),
        "entry_price": evidence.get("entry_price"),
        "entry_price_source": evidence.get("entry_price_source"),
        "entry_price_basis": evidence.get("entry_price_basis") or evidence.get("price_basis"),
        "entry_date": evidence.get("entry_date") or trade_date,
        "entry_time": (evidence.get("execution_contract") or {}).get("execution_time"),
        "t1_open_price": evidence.get("exit_open"),
        "t1_high_price": evidence.get("exit_high"),
        "t1_low_price": evidence.get("exit_low"),
        "t1_close_price": evidence.get("exit_close"),
        "label_status": evidence.get("label_status") or canonical_labels.get("label_status"),
        "label_version": evidence.get("label_version"),
        "label_source": evidence.get("source") or evidence.get("market_data_source"),
        "label_generated_at": evidence.get("generated_at"),
        "market_data_source": evidence.get("market_data_source") or evidence.get("source"),
        "price_adjustment_mode": evidence.get("price_basis") or evidence.get("kline_adjustment"),
        "trading_calendar_source": evidence.get("trading_calendar_source") or "xiaogu_scheduler",
        "production_run_id": production_run_id,
        "candidate_snapshot_id": candidate_snapshot_id or None,
        "return_status": return_status or ("SETTLED" if t1_close_return is not None else "PENDING"),
        "settlement_evidence": json.dumps(evidence, ensure_ascii=False, default=str),
    }
    candidate_settlement_payload = {
        key: value
        for key, value in {
            "t1_return": t1_close_return,
            "t1_return_close": payload["t1_return_close"],
            "t1_return_high": payload["t1_return_high"],
            "is_limit_up": is_limit_up,
            "return_status": payload["return_status"],
            "settlement_evidence": evidence,
        }.items()
        if value is not None
    }
    t1_metrics = canonical_labels
    for key in (
        't1_open_return', 't1_high_return', 't1_low_return',
        't1_close_return', 't1_mfe', 't1_mae',
    ):
        if t1_metrics.get(key) is not None:
            candidate_settlement_payload[key] = t1_metrics[key]

    def persist_candidate_settlement(active_db: Any) -> None:
        if not production:
            return
        result = active_db.execute(
            text("""
                UPDATE daily_candidates
                SET future_return_fields_placeholder =
                        COALESCE(future_return_fields_placeholder, CAST('{}' AS jsonb))
                        || CAST(:candidate_settlement_payload AS jsonb),
                    updated_at = NOW()
                WHERE production_run_id = :production_run_id
                  AND candidate_snapshot_id = :candidate_snapshot_id
                  AND symbol = :symbol
            """),
            {
                **payload,
                "candidate_settlement_payload": json.dumps(
                    candidate_settlement_payload,
                    ensure_ascii=False,
                    default=str,
                ),
            },
        )
        if getattr(result, "rowcount", 1) == 0:
            raise ValueError("PRODUCTION_RETURN_CANDIDATE_NOT_FOUND")

    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        if production:
            existing = active_db.execute(
                text("""
                        SELECT id, t1_return, candidate_snapshot_id, label_version, t1_close_return
                    FROM returns
                    WHERE production_run_id = :production_run_id AND symbol = :symbol
                    FOR UPDATE
                """),
                payload,
            ).fetchone()
            if existing:
                current_t1 = existing[1]
                current_snapshot = str(existing[2] or "")
                current_label_version = str(existing[3] or "") if len(existing) > 3 else ""
                current_canonical_close = existing[4] if len(existing) > 4 else None
                if current_snapshot and candidate_snapshot_id and current_snapshot != candidate_snapshot_id:
                    raise ValueError("PRODUCTION_RETURN_CANDIDATE_SNAPSHOT_MISMATCH")
                if (
                    current_label_version == "canonical_t1_v1"
                    and current_canonical_close is not None
                    and t1_close_return is not None
                    and float(current_canonical_close) != float(t1_close_return)
                ):
                    raise ValueError("PRODUCTION_T1_RETURN_ALREADY_SETTLED")
                active_db.execute(
                    text("""
                        UPDATE returns
                        SET pick_id = COALESCE(:pick_id, pick_id),
                            candidate_snapshot_id = COALESCE(:candidate_snapshot_id, candidate_snapshot_id),
                            t1_return = CASE
                                WHEN label_version IS NULL OR label_version <> 'canonical_t1_v1'
                                    THEN t1_return
                                ELSE COALESCE(t1_return, :t1_return)
                            END,
                            t1_return_close = COALESCE(:t1_return_close, t1_return_close),
                            t1_return_high = COALESCE(:t1_return_high, t1_return_high),
                            next_day_open_return = COALESCE(:next_day_open_return, next_day_open_return),
                            next_day_high_return = COALESCE(:next_day_high_return, next_day_high_return),
                            next_day_low_return = COALESCE(:next_day_low_return, next_day_low_return),
                            next_day_gap_return = COALESCE(:next_day_gap_return, next_day_gap_return),
                            next_day_drawdown = COALESCE(:next_day_drawdown, next_day_drawdown),
                            high_to_close_retrace = COALESCE(:high_to_close_retrace, high_to_close_retrace),
                            t1_open_return = COALESCE(:t1_open_return, t1_open_return),
                            t1_high_return = COALESCE(:t1_high_return, t1_high_return),
                            t1_low_return = COALESCE(:t1_low_return, t1_low_return),
                            t1_close_return = COALESCE(:t1_close_return, t1_close_return),
                            t1_mfe = COALESCE(:t1_mfe, t1_mfe),
                            t1_mae = COALESCE(:t1_mae, t1_mae),
                            t1_vwap_return = COALESCE(:t1_vwap_return, t1_vwap_return),
                            t1_gap_return = COALESCE(:t1_gap_return, t1_gap_return),
                            t1_net_return = COALESCE(:t1_net_return, t1_net_return),
                            slippage = COALESCE(:slippage, slippage),
                            commission = COALESCE(:commission, commission),
                            stamp_duty = COALESCE(:stamp_duty, stamp_duty),
                            transfer_fee = COALESCE(:transfer_fee, transfer_fee),
                            market_impact = COALESCE(:market_impact, market_impact),
                            entry_price = COALESCE(:entry_price, entry_price),
                            entry_price_source = COALESCE(:entry_price_source, entry_price_source),
                            entry_price_basis = COALESCE(:entry_price_basis, entry_price_basis),
                            entry_date = COALESCE(:entry_date, entry_date),
                            entry_time = COALESCE(:entry_time, entry_time),
                            t1_open_price = COALESCE(:t1_open_price, t1_open_price),
                            t1_high_price = COALESCE(:t1_high_price, t1_high_price),
                            t1_low_price = COALESCE(:t1_low_price, t1_low_price),
                            t1_close_price = COALESCE(:t1_close_price, t1_close_price),
                            label_status = COALESCE(:label_status, label_status),
                            label_version = COALESCE(:label_version, label_version),
                            label_source = COALESCE(:label_source, label_source),
                            label_generated_at = COALESCE(:label_generated_at, label_generated_at),
                            market_data_source = COALESCE(:market_data_source, market_data_source),
                            price_adjustment_mode = COALESCE(:price_adjustment_mode, price_adjustment_mode),
                            trading_calendar_source = COALESCE(:trading_calendar_source, trading_calendar_source),
                            return_status = CASE
                                WHEN t1_return IS NOT NULL OR :t1_return IS NOT NULL THEN 'SETTLED'
                                ELSE :return_status
                            END,
                            settlement_evidence = COALESCE(settlement_evidence, CAST('{}' AS jsonb))
                                || CAST(:settlement_evidence AS jsonb),
                            filled_at = NOW(),
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    {**payload, "id": existing[0]},
                )
                persist_candidate_settlement(active_db)
                return int(existing[0])
            row = active_db.execute(
                text("""
                    INSERT INTO returns (
                        pick_id, trade_date, symbol, t1_return, t2_return, t3_return, t5_return,
                        t1_return_close, t1_return_high, next_day_open_return, next_day_high_return,
                        next_day_low_return, next_day_gap_return, next_day_drawdown, high_to_close_retrace,
                        t1_open_return, t1_high_return, t1_low_return, t1_close_return, t1_mfe, t1_mae,
                        t1_vwap_return, t1_gap_return, t1_net_return, slippage, commission, stamp_duty,
                        transfer_fee, market_impact,
                        entry_price, entry_price_source, entry_price_basis, entry_date, entry_time,
                        t1_open_price, t1_high_price, t1_low_price, t1_close_price,
                        label_status, label_version, label_source, label_generated_at,
                        market_data_source, price_adjustment_mode, trading_calendar_source,
                        production_run_id, candidate_snapshot_id, return_status, settlement_evidence
                    ) VALUES (
                        :pick_id, :trade_date, :symbol, :t1_return, :t2_return, :t3_return, :t5_return,
                        :t1_return_close, :t1_return_high, :next_day_open_return, :next_day_high_return,
                        :next_day_low_return, :next_day_gap_return, :next_day_drawdown, :high_to_close_retrace,
                        :t1_open_return, :t1_high_return, :t1_low_return, :t1_close_return, :t1_mfe, :t1_mae,
                        :t1_vwap_return, :t1_gap_return, :t1_net_return, :slippage, :commission, :stamp_duty,
                        :transfer_fee, :market_impact,
                        :entry_price, :entry_price_source, :entry_price_basis, :entry_date, :entry_time,
                        :t1_open_price, :t1_high_price, :t1_low_price, :t1_close_price,
                        :label_status, :label_version, :label_source, :label_generated_at,
                        :market_data_source, :price_adjustment_mode, :trading_calendar_source,
                        :production_run_id, :candidate_snapshot_id, :return_status,
                        CAST(:settlement_evidence AS jsonb)
                    )
                    ON CONFLICT (production_run_id, symbol) WHERE production_run_id IS NOT NULL
                    DO NOTHING
                    RETURNING id
                """),
                payload,
            ).fetchone()
            if row:
                persist_candidate_settlement(active_db)
                return int(row[0])
            raise RuntimeError("PRODUCTION_RETURN_CONCURRENT_INSERT")

        resolved_pick_id = pick_id if pick_id is not None else resolve_pick_id(trade_date, normalized_symbol)
        row = active_db.execute(
            text("""
                INSERT INTO returns (
                    pick_id, trade_date, symbol, t1_return, t2_return, t3_return, t5_return,
                    t1_return_close, t1_return_high, next_day_open_return, next_day_high_return,
                    next_day_low_return, next_day_gap_return, next_day_drawdown, high_to_close_retrace,
                    t1_open_return, t1_high_return, t1_low_return, t1_close_return, t1_mfe, t1_mae,
                    t1_vwap_return, t1_gap_return, t1_net_return, slippage, commission, stamp_duty,
                    transfer_fee, market_impact,
                    entry_price, entry_price_source, entry_price_basis, entry_date, entry_time,
                    t1_open_price, t1_high_price, t1_low_price, t1_close_price,
                    label_status, label_version, label_source, label_generated_at,
                    market_data_source, price_adjustment_mode, trading_calendar_source,
                    return_status, settlement_evidence
                ) VALUES (
                    :pick_id, :trade_date, :symbol, :t1_return, :t2_return, :t3_return, :t5_return,
                    :t1_return_close, :t1_return_high, :next_day_open_return, :next_day_high_return,
                    :next_day_low_return, :next_day_gap_return, :next_day_drawdown, :high_to_close_retrace,
                    :t1_open_return, :t1_high_return, :t1_low_return, :t1_close_return, :t1_mfe, :t1_mae,
                    :t1_vwap_return, :t1_gap_return, :t1_net_return, :slippage, :commission, :stamp_duty,
                    :transfer_fee, :market_impact,
                    :entry_price, :entry_price_source, :entry_price_basis, :entry_date, :entry_time,
                    :t1_open_price, :t1_high_price, :t1_low_price, :t1_close_price,
                    :label_status, :label_version, :label_source, :label_generated_at,
                    :market_data_source, :price_adjustment_mode, :trading_calendar_source,
                    :return_status, CAST(:settlement_evidence AS jsonb)
                )
                ON CONFLICT (trade_date, symbol) WHERE production_run_id IS NULL
                DO UPDATE SET
                    pick_id = COALESCE(EXCLUDED.pick_id, returns.pick_id),
                    t1_return = COALESCE(returns.t1_return, EXCLUDED.t1_return),
                    t2_return = COALESCE(returns.t2_return, EXCLUDED.t2_return),
                    t3_return = COALESCE(returns.t3_return, EXCLUDED.t3_return),
                    t5_return = COALESCE(returns.t5_return, EXCLUDED.t5_return),
                    t1_return_close = COALESCE(returns.t1_return_close, EXCLUDED.t1_return_close),
                    t1_return_high = COALESCE(returns.t1_return_high, EXCLUDED.t1_return_high),
                    next_day_open_return = COALESCE(returns.next_day_open_return, EXCLUDED.next_day_open_return),
                    next_day_high_return = COALESCE(returns.next_day_high_return, EXCLUDED.next_day_high_return),
                    next_day_low_return = COALESCE(returns.next_day_low_return, EXCLUDED.next_day_low_return),
                    t1_open_return = COALESCE(returns.t1_open_return, EXCLUDED.t1_open_return),
                    t1_high_return = COALESCE(returns.t1_high_return, EXCLUDED.t1_high_return),
                    t1_low_return = COALESCE(returns.t1_low_return, EXCLUDED.t1_low_return),
                    t1_close_return = COALESCE(returns.t1_close_return, EXCLUDED.t1_close_return),
                    t1_mfe = COALESCE(returns.t1_mfe, EXCLUDED.t1_mfe),
                    t1_mae = COALESCE(returns.t1_mae, EXCLUDED.t1_mae),
                    t1_vwap_return = COALESCE(returns.t1_vwap_return, EXCLUDED.t1_vwap_return),
                    t1_gap_return = COALESCE(returns.t1_gap_return, EXCLUDED.t1_gap_return),
                    t1_net_return = COALESCE(returns.t1_net_return, EXCLUDED.t1_net_return),
                    slippage = COALESCE(returns.slippage, EXCLUDED.slippage),
                    commission = COALESCE(returns.commission, EXCLUDED.commission),
                    stamp_duty = COALESCE(returns.stamp_duty, EXCLUDED.stamp_duty),
                    transfer_fee = COALESCE(returns.transfer_fee, EXCLUDED.transfer_fee),
                    market_impact = COALESCE(returns.market_impact, EXCLUDED.market_impact),
                    entry_price = COALESCE(returns.entry_price, EXCLUDED.entry_price),
                    entry_price_source = COALESCE(returns.entry_price_source, EXCLUDED.entry_price_source),
                    entry_price_basis = COALESCE(returns.entry_price_basis, EXCLUDED.entry_price_basis),
                    entry_date = COALESCE(returns.entry_date, EXCLUDED.entry_date),
                    entry_time = COALESCE(returns.entry_time, EXCLUDED.entry_time),
                    t1_open_price = COALESCE(returns.t1_open_price, EXCLUDED.t1_open_price),
                    t1_high_price = COALESCE(returns.t1_high_price, EXCLUDED.t1_high_price),
                    t1_low_price = COALESCE(returns.t1_low_price, EXCLUDED.t1_low_price),
                    t1_close_price = COALESCE(returns.t1_close_price, EXCLUDED.t1_close_price),
                    label_status = COALESCE(returns.label_status, EXCLUDED.label_status),
                    label_version = COALESCE(returns.label_version, EXCLUDED.label_version),
                    label_source = COALESCE(returns.label_source, EXCLUDED.label_source),
                    label_generated_at = COALESCE(returns.label_generated_at, EXCLUDED.label_generated_at),
                    market_data_source = COALESCE(returns.market_data_source, EXCLUDED.market_data_source),
                    price_adjustment_mode = COALESCE(returns.price_adjustment_mode, EXCLUDED.price_adjustment_mode),
                    trading_calendar_source = COALESCE(returns.trading_calendar_source, EXCLUDED.trading_calendar_source),
                    return_status = CASE
                        WHEN returns.t1_return IS NOT NULL OR EXCLUDED.t1_return IS NOT NULL THEN 'SETTLED'
                        ELSE EXCLUDED.return_status
                    END,
                        settlement_evidence = COALESCE(returns.settlement_evidence, CAST('{}' AS jsonb))
                        || EXCLUDED.settlement_evidence,
                    filled_at = NOW(),
                    updated_at = NOW()
                RETURNING id
            """),
            {**payload, "pick_id": resolved_pick_id},
        ).fetchone()
        return int(row[0]) if row else -1


def record_return_backfill_failure(
    trade_date: date,
    symbol: str,
    reason: str,
    *,
    return_horizon: str = 'T+1',
    production_run_id: Optional[str] = None,
    candidate_snapshot_id: str = "",
    db: Any | None = None,
) -> None:
    """Persist an explicit, resumable return-backfill failure on the candidate row."""
    normalized_reason = str(reason or 'UNKNOWN')
    payload = {
        'symbol': str(symbol),
        'trade_date': trade_date.isoformat() if hasattr(trade_date, 'isoformat') else str(trade_date),
        'return_horizon': return_horizon,
        'status': 'FAILED',
        'failure_reason': normalized_reason,
        'last_attempt_at': datetime.now(timezone.utc).isoformat(),
        'payload': {
            'provider': 'baostock',
            'error_type': normalized_reason,
        },
    }
    if production_run_id and not candidate_snapshot_id:
        raise ValueError("PRODUCTION_RETURN_CANDIDATE_SNAPSHOT_REQUIRED")
    where = """
        production_run_id = :production_run_id
        AND candidate_snapshot_id = :candidate_snapshot_id
        AND symbol = :symbol
    """ if production_run_id else """
        production_run_id IS NULL
        AND trade_date = :trade_date
        AND symbol = :symbol
    """
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        active_db.execute(
            text("""
                UPDATE daily_candidates
                SET future_return_fields_placeholder =
                    COALESCE(future_return_fields_placeholder, CAST('{}' AS jsonb))
                    || CAST(:payload AS jsonb),
                    updated_at = NOW()
                WHERE """ + where),
            {
                'trade_date': trade_date,
                'symbol': symbol,
                'production_run_id': production_run_id,
                'candidate_snapshot_id': candidate_snapshot_id,
                'payload': json.dumps({'return_backfill_failure': payload}, ensure_ascii=False),
            },
        )


def update_candidate_cohort(trade_date: date, symbol: str, cohort: Dict[str, Any], provenance: Optional[Dict[str, Any]] = None) -> None:
    """Persist a classification-only cohort update without rewriting evidence."""
    provenance = provenance or {}
    with get_db() as db:
        db.execute(
            text("""
                UPDATE daily_candidates
                SET cohort = :cohort,
                    cohort_quality = :cohort_quality,
                    cohort_status_flags = CAST(:status_flags AS jsonb),
                    reconstruction_provenance = CASE
                        WHEN reconstruction_provenance IS NULL OR reconstruction_provenance = CAST('{}' AS jsonb)
                        THEN CAST(:provenance AS jsonb)
                        ELSE reconstruction_provenance
                    END,
                    updated_at = NOW()
                WHERE trade_date = :trade_date AND symbol = :symbol
            """),
            {
                'trade_date': trade_date, 'symbol': symbol,
                'cohort': cohort.get('cohort') or '',
                'cohort_quality': cohort.get('cohort_quality') or '',
                'status_flags': json.dumps(cohort.get('status_flags') or [], ensure_ascii=False),
                'provenance': json.dumps(provenance, ensure_ascii=False),
            },
        )


def insert_scan_session(
    trade_date: date,
    scan_time: Any,
    source_id: str,
    quotes_count: int,
    scored_count: int,
    passed_count: int,
    scan_dir: str,
    market_snapshot: Optional[Dict[str, Any]] = None,
    source_status: Optional[Dict[str, Any]] = None,
    source_counts: Optional[Dict[str, Any]] = None,
    source_diagnostics: Optional[Dict[str, Any]] = None,
    production_run_id: Optional[str] = None,
    db: Any | None = None,
) -> int:
    json_values = {
        "market_snapshot": json.dumps(market_snapshot, ensure_ascii=False, default=str) if market_snapshot is not None else None,
        "source_status": json.dumps(source_status, ensure_ascii=False, default=str) if source_status is not None else None,
        "source_counts": json.dumps(source_counts, ensure_ascii=False, default=str) if source_counts is not None else None,
        "source_diagnostics": json.dumps(source_diagnostics, ensure_ascii=False, default=str) if source_diagnostics is not None else None,
    }
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        if production_run_id:
            existing = active_db.execute(
                text("""
                    SELECT id
                    FROM scan_sessions
                    WHERE production_run_id = :production_run_id
                    ORDER BY scan_time DESC, id DESC
                    LIMIT 1
                """),
                {"production_run_id": production_run_id},
            ).fetchone()
            if existing:
                active_db.execute(
                    text("""
                        UPDATE scan_sessions
                        SET scan_time = :scan_time,
                            source_id = :source_id,
                            quotes_count = :quotes_count,
                            scored_count = :scored_count,
                            passed_count = :passed_count,
                            scan_dir = :scan_dir,
                            market_snapshot = COALESCE(CAST(:market_snapshot AS jsonb), market_snapshot),
                            source_status = COALESCE(CAST(:source_status AS jsonb), source_status),
                            source_counts = COALESCE(CAST(:source_counts AS jsonb), source_counts),
                            source_diagnostics = COALESCE(CAST(:source_diagnostics AS jsonb), source_diagnostics),
                            status = 'completed',
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    {
                        "id": existing[0],
                        "scan_time": scan_time,
                        "source_id": source_id,
                        "quotes_count": quotes_count,
                        "scored_count": scored_count,
                        "passed_count": passed_count,
                        "scan_dir": scan_dir,
                        **json_values,
                    },
                )
                return int(existing[0])
            result = active_db.execute(
                text("""
                    INSERT INTO scan_sessions (
                        trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, scan_dir,
                        market_snapshot, source_status, source_counts, source_diagnostics, production_run_id
                    ) VALUES (
                        :trade_date, :scan_time, :source_id, :quotes_count, :scored_count, :passed_count, :scan_dir,
                        CAST(:market_snapshot AS jsonb), CAST(:source_status AS jsonb),
                        CAST(:source_counts AS jsonb), CAST(:source_diagnostics AS jsonb), :production_run_id
                    ) RETURNING id
                """),
                {
                    'trade_date': trade_date, 'scan_time': scan_time, 'source_id': source_id,
                    'quotes_count': quotes_count, 'scored_count': scored_count, 'passed_count': passed_count,
                    'scan_dir': scan_dir, 'production_run_id': production_run_id, **json_values,
                },
            )
            row = result.fetchone()
            return int(row[0]) if row else -1
        existing = active_db.execute(
            text("""
                SELECT id
                FROM scan_sessions
                WHERE trade_date = :trade_date AND scan_dir = :scan_dir
                ORDER BY scan_time DESC, id DESC
                LIMIT 1
            """),
            {"trade_date": trade_date, "scan_dir": scan_dir},
        ).fetchone()
        if existing:
            active_db.execute(
                text("""
                    UPDATE scan_sessions
                    SET scan_time = :scan_time,
                        source_id = :source_id,
                        quotes_count = :quotes_count,
                        scored_count = :scored_count,
                        passed_count = :passed_count,
                        market_snapshot = COALESCE(CAST(:market_snapshot AS jsonb), market_snapshot),
                        source_status = COALESCE(CAST(:source_status AS jsonb), source_status),
                        source_counts = COALESCE(CAST(:source_counts AS jsonb), source_counts),
                        source_diagnostics = COALESCE(CAST(:source_diagnostics AS jsonb), source_diagnostics),
                        status = 'completed',
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": existing[0],
                    "scan_time": scan_time,
                    "source_id": source_id,
                    "quotes_count": quotes_count,
                    "scored_count": scored_count,
                    "passed_count": passed_count,
                    **json_values,
                },
            )
            return existing[0]
        result = active_db.execute(
            text("""
                INSERT INTO scan_sessions
                    (
                        trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, scan_dir,
                        market_snapshot, source_status, source_counts, source_diagnostics
                    )
                VALUES (
                    :trade_date, :scan_time, :source_id, :quotes_count, :scored_count, :passed_count, :scan_dir,
                    CAST(:market_snapshot AS jsonb), CAST(:source_status AS jsonb),
                    CAST(:source_counts AS jsonb), CAST(:source_diagnostics AS jsonb)
                )
                RETURNING id
            """),
            {
                "trade_date": trade_date,
                "scan_time": scan_time,
                "source_id": source_id,
                "quotes_count": quotes_count,
                "scored_count": scored_count,
                "passed_count": passed_count,
                "scan_dir": scan_dir,
                "market_snapshot": json_values["market_snapshot"] or "{}",
                "source_status": json_values["source_status"] or "{}",
                "source_counts": json_values["source_counts"] or "{}",
                "source_diagnostics": json_values["source_diagnostics"] or "{}",
            }
        )
        row = result.fetchone()
        return row[0] if row else -1


def upsert_scan_market_data(
    scan_session_id: int,
    trade_date: date,
    scan_time: Any,
    domain_rows: Dict[str, Any],
    domain_diagnostics: Optional[Dict[str, Any]] = None,
    data_version: str = "eastmoney_api_scan_v2",
) -> int:
    """Persist full raw per-domain scan payloads for deterministic replay."""
    written = 0
    diagnostics = domain_diagnostics or {}
    with get_db() as db:
        for domain, rows in domain_rows.items():
            payload = rows if isinstance(rows, (list, dict)) else []
            item_count = len(payload) if isinstance(payload, list) else (
                sum(len(value) for value in payload.values() if isinstance(value, list))
                if isinstance(payload, dict) else 0
            )
            db.execute(
                text("""
                    INSERT INTO scan_market_data (
                        scan_session_id, trade_date, scan_time, domain, item_count,
                        payload, source_metadata, data_version
                    )
                    VALUES (
                        :scan_session_id, :trade_date, :scan_time, :domain, :item_count,
                        CAST(:payload AS jsonb), CAST(:source_metadata AS jsonb), :data_version
                    )
                    ON CONFLICT (scan_session_id, domain) DO UPDATE SET
                        scan_time = EXCLUDED.scan_time,
                        item_count = EXCLUDED.item_count,
                        payload = EXCLUDED.payload,
                        source_metadata = EXCLUDED.source_metadata,
                        data_version = EXCLUDED.data_version,
                        updated_at = NOW()
                """),
                {
                    "scan_session_id": scan_session_id,
                    "trade_date": trade_date,
                    "scan_time": scan_time,
                    "domain": domain,
                    "item_count": item_count,
                    "payload": json.dumps(payload, ensure_ascii=False, default=str),
                    "source_metadata": json.dumps(diagnostics.get(domain) or {}, ensure_ascii=False, default=str),
                    "data_version": data_version,
                },
            )
            written += 1
    return written


def fetch_latest_scan_session(
    trade_date: date,
    *,
    production_run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    run_clause = (
        "AND production_run_id = :production_run_id"
        if production_run_id else ""
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT
                        id, trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, scan_dir, status,
                        market_snapshot, source_status, source_counts, source_diagnostics, production_run_id
                    FROM scan_sessions
                    WHERE trade_date = :trade_date
                    """ + run_clause + """
                    ORDER BY scan_time DESC, id DESC
                    LIMIT 1
                """),
                {"trade_date": trade_date, "production_run_id": production_run_id},
            ).mappings().first()
    except Exception:
        if production_run_id:
            raise
        # Pre-run-lineage databases remain readable for historical audit only.
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT
                        id, trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, scan_dir, status,
                        market_snapshot, source_status, source_counts, source_diagnostics
                    FROM scan_sessions
                    WHERE trade_date = :trade_date
                    ORDER BY scan_time DESC, id DESC
                    LIMIT 1
                """),
                {"trade_date": trade_date},
            ).mappings().first()
            if row:
                row = dict(row)
                row["production_run_id"] = None
    return dict(row) if row else None


def fetch_latest_api_scan_session_with_market_data(trade_date: date) -> Optional[Dict[str, Any]]:
    """Return the latest API scan session that has raw-domain payload rows."""
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    ss.id, ss.trade_date, ss.scan_time, ss.source_id, ss.quotes_count, ss.scored_count,
                    ss.passed_count, ss.scan_dir, ss.status, ss.market_snapshot, ss.source_status,
                    ss.source_counts, ss.source_diagnostics,
                    COUNT(smd.id) AS raw_domain_count
                FROM scan_sessions ss
                JOIN scan_market_data smd ON smd.scan_session_id = ss.id
                WHERE ss.trade_date = :trade_date
                  AND ss.source_id = 'eastmoney_api_scan_v2'
                GROUP BY ss.id
                ORDER BY ss.scan_time DESC, ss.id DESC
                LIMIT 1
            """),
            {"trade_date": trade_date},
        ).mappings().first()
    return dict(row) if row else None


def fetch_scan_market_data_payloads(scan_session_id: int) -> Dict[str, Any]:
    """Load raw-domain payloads for one scan session keyed by domain."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT domain, payload, item_count, source_metadata, data_version
                FROM scan_market_data
                WHERE scan_session_id = :scan_session_id
                ORDER BY domain
            """),
            {"scan_session_id": scan_session_id},
        ).mappings().all()
    payloads: Dict[str, Any] = {}
    for row in rows:
        domain = str(row.get("domain") or "").strip()
        if domain:
            payloads[domain] = row.get("payload")
    return payloads


def fetch_daily_candidates(
    trade_date: date,
    *,
    production_run_id: Optional[str] = None,
    lightweight: bool = False,
) -> List[Dict[str, Any]]:
    run_clause = (
        "AND production_run_id = :production_run_id"
        if production_run_id else ""
    )
    snapshot_columns = (
        """
                    candidate_features - 'selection_diagnostics' - 'candidate_pool_context'
                        AS candidate_features,
                    factor_snapshot - 'candidate_pool_context' AS factor_snapshot,
                    eligibility_snapshot,
                    ranking_basis - 'candidate_pool_context' AS ranking_basis,
                    auxiliary_evidence_snapshot
        """
        if lightweight else
        """
                    eligibility_snapshot, selection_diagnostics, raw_json,
                    source_layers, candidate_features, candidate_entry_reason,
                    ticket_reason, not_selected_reason, factor_snapshot,
                    auxiliary_evidence_snapshot, ranking_basis, postmortem_snapshot,
                    future_return_fields_placeholder
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT
                    trade_date, symbol, stock_name, rank, final_score, is_official_pick, decision,
                    production_run_id, candidate_snapshot_id,
                    open_price, close_price, high_price, low_price, volume, amount, pct_chg, turnover_rate,
                    signal_pct, close_position_score, fund_flow_momentum, sector_catalyst_score,
                    early_opportunity_score, topic_propagation_score, market_regime, sentiment_catalyst,
                    theme_catalyst, news_catalyst, positive_catalyst, selection_reason,
                    selection_outcome, selection_outcome_reason, blockers, hard_gate_status,
                    {snapshot_columns},
                    cohort, cohort_quality, cohort_status_flags, reconstruction_provenance
                FROM daily_candidates
                WHERE trade_date = :trade_date
                """ + run_clause + """
                ORDER BY COALESCE(rank, 999999), COALESCE(final_score, 0) DESC, symbol
            """),
            {"trade_date": trade_date, "production_run_id": production_run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def fetch_picks(
    trade_date: date,
    *,
    include_superseded: bool = False,
    production_run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    run_clause = (
        "AND production_run_id = :production_run_id"
        if production_run_id else ""
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    trade_date, symbol, decision, final_score, blockers, features, source_layers,
                    id, production_run_id, formal_rank_snapshot_id, formal_rank_snapshot_version,
                    rule_version, scan_dir, dry_run, paper_only, no_trade, created_at, updated_at,
                    stock_name, rank, structured_score, ranking_basis, ticket_reason, selection_reason,
                    paper_pick_eligibility, official_target_exclusion_reasons, risk_flags,
                    auxiliary_evidence_status, information_coverage_audit_snapshot, source_summary_path
                FROM picks
                WHERE trade_date = :trade_date
                  """ + run_clause + """
                  AND (
                      :include_superseded
                      OR COALESCE(features ->> 'superseded', 'false') <> 'true'
                  )
                ORDER BY updated_at DESC NULLS LAST, created_at DESC, COALESCE(final_score, 0) DESC, symbol, id
            """),
            {
                "trade_date": trade_date,
                "include_superseded": include_superseded,
                "production_run_id": production_run_id,
            },
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_returns(
    trade_date: date,
    *,
    production_run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    run_clause = (
        "AND production_run_id = :production_run_id"
        if production_run_id else ""
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    trade_date, symbol, pick_id, t1_return, t2_return, t3_return, t5_return,
                    production_run_id, candidate_snapshot_id, return_status, settlement_evidence,
                    is_limit_up, t1_return_close, t1_return_high, next_day_open_return, next_day_high_return,
                    next_day_low_return, next_day_gap_return, next_day_drawdown,
                    high_to_close_retrace,
                    t1_open_return, t1_high_return, t1_low_return, t1_close_return, t1_mfe, t1_mae,
                    entry_price, entry_price_source, entry_price_basis, entry_date, entry_time,
                    t1_open_price, t1_high_price, t1_low_price, t1_close_price,
                    label_status, label_version, label_source, label_generated_at,
                    market_data_source, price_adjustment_mode, trading_calendar_source,
                    filled_at, created_at, updated_at
                FROM returns
                WHERE trade_date = :trade_date
                """ + run_clause + """
                ORDER BY symbol, id
            """),
            {"trade_date": trade_date, "production_run_id": production_run_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_available_trade_dates() -> List[date]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT DISTINCT trade_date
                FROM (
                    SELECT trade_date FROM picks
                    UNION
                    SELECT trade_date FROM daily_candidates
                    UNION
                    SELECT trade_date FROM returns
                ) dates
                ORDER BY trade_date
            """)
        ).fetchall()
    return [row[0] for row in rows]


def fetch_signals(
    trade_date: date,
    *,
    production_run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    run_clause = (
        "AND production_run_id = :production_run_id"
        if production_run_id else ""
    )
    params = {"trade_date": trade_date, "production_run_id": production_run_id}
    statement = text("""
        SELECT trade_date, symbol, signal_key, signal_value, raw_json, production_run_id
        FROM signals
        WHERE trade_date = :trade_date
        """ + run_clause + """
        ORDER BY symbol, signal_key
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(statement, params).mappings().all()
    except Exception as exc:
        # Historical/replay reads may target a pre-lineage schema.  A
        # production-scoped read must fail loudly rather than silently
        # joining an ambiguous legacy signal snapshot.  PostgreSQL marks the
        # connection transaction failed after an unknown-column error, so the
        # fallback must use a fresh connection.
        if production_run_id or "production_run_id" not in str(exc):
            raise
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT trade_date, symbol, signal_key, signal_value, raw_json,
                           NULL::varchar AS production_run_id
                    FROM signals
                    WHERE trade_date = :trade_date
                    ORDER BY symbol, signal_key
                """),
                {"trade_date": trade_date},
            ).mappings().all()
    return [dict(row) for row in rows]


def upsert_signal(
    trade_date: date,
    symbol: str,
    signal_key: str,
    signal_value: Optional[float] = None,
    raw_json: Optional[Dict[str, Any]] = None,
    production_run_id: Optional[str] = None,
    db: Any | None = None,
) -> None:
    if production_run_id:
        statement = text("""
            INSERT INTO signals (
                trade_date, symbol, signal_key, signal_value, raw_json, production_run_id
            ) VALUES (
                :trade_date, :symbol, :signal_key, :signal_value,
                CAST(:raw_json AS jsonb), :production_run_id
            )
            ON CONFLICT (production_run_id, symbol, signal_key)
            WHERE production_run_id IS NOT NULL
            DO UPDATE SET
                signal_value = EXCLUDED.signal_value,
                raw_json = EXCLUDED.raw_json,
                updated_at = NOW()
        """)
    else:
        statement = text("""
            INSERT INTO signals (trade_date, symbol, signal_key, signal_value, raw_json)
            VALUES (:trade_date, :symbol, :signal_key, :signal_value, CAST(:raw_json AS jsonb))
            ON CONFLICT (trade_date, symbol, signal_key)
            WHERE production_run_id IS NULL
            DO UPDATE SET
                signal_value = EXCLUDED.signal_value,
                raw_json = EXCLUDED.raw_json,
                updated_at = NOW()
        """)
    payload = {
        "trade_date": trade_date,
        "symbol": symbol,
        "signal_key": signal_key,
        "signal_value": signal_value,
        "raw_json": json.dumps(raw_json or {}, ensure_ascii=False, default=str),
        "production_run_id": production_run_id,
    }
    if db is None:
        with get_db() as active_db:
            active_db.execute(statement, payload)
    else:
        db.execute(statement, payload)


def upsert_limitup_gene_signals(
    trade_date: date,
    symbol: str,
    candidate: Dict[str, Any],
    *,
    db: Any | None = None,
    production_run_id: Optional[str] = None,
) -> Dict[str, bool]:
    """Persist every pre-decision limit-up gene flag, including explicit false values."""
    signal_values = limitup_gene_signal_values(candidate)
    context = get_db() if db is None else nullcontext(db)
    with context as active_db:
        for signal_key in LIMITUP_GENE_SHADOW_SIGNALS:
            signal_value = signal_values[signal_key]
            upsert_signal(
                trade_date=trade_date,
                symbol=symbol,
                signal_key=signal_key,
                signal_value=1.0 if signal_value else 0.0,
                raw_json={
                    "value": signal_value,
                    "source": "decision_snapshot",
                    "pre_decision": True,
                    "production_run_id": production_run_id,
                },
                production_run_id=production_run_id,
                db=active_db,
            )
    return signal_values


def fetch_scan_data_directory_content(trade_date: date) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    trade_date, scan_time, section_key, section_title, section_url, item_key, item_title, item_url,
                    page_url, page_title, table_index, row_index, row_key, code, title, summary, cells, raw_json
                FROM scan_data_directory_content
                WHERE trade_date = :trade_date
                ORDER BY COALESCE(item_key, ''), COALESCE(table_index, 0), COALESCE(row_index, 0), row_key
            """),
            {"trade_date": trade_date},
        ).mappings().all()
    return [dict(row) for row in rows]


def fetch_scan_data_directory_catalog(trade_date: date) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    trade_date, scan_time, scan_session_id, section_key, section_title, section_url, section_index,
                    item_key, item_title, item_url, item_index, record_key, title, summary, raw_json
                FROM scan_data_directory_catalog
                WHERE trade_date = :trade_date
                ORDER BY COALESCE(section_index, 0), COALESCE(item_index, 0), record_key
            """),
            {"trade_date": trade_date},
        ).mappings().all()
    return [dict(row) for row in rows]


def upsert_scan_data_directory_records(
    scan_session_id: int,
    trade_date: date,
    scan_time: Any,
    catalog_records: List[Dict[str, Any]],
    content_records: List[Dict[str, Any]],
    db: Any | None = None,
) -> None:
    context = db if db is not None else get_db()
    with context as active_db:
        for record in catalog_records:
            record_key = str(record.get('record_key') or '').strip()
            if not record_key:
                record_key = _stable_directory_record_key(
                    'catalog',
                    trade_date,
                    record,
                    ['section_key', 'item_key', 'item_title', 'item_url', 'title', 'summary'],
                )
            active_db.execute(
                text("""
                    INSERT INTO scan_data_directory_catalog (
                        trade_date, scan_time, scan_session_id, section_key, section_title, section_url, section_index,
                        item_key, item_title, item_url, item_index, record_key, title, summary, raw_json
                    ) VALUES (
                        :trade_date, :scan_time, :scan_session_id, :section_key, :section_title, :section_url, :section_index,
                        :item_key, :item_title, :item_url, :item_index, :record_key, :title, :summary, :raw_json
                    )
                    ON CONFLICT (trade_date, record_key) DO UPDATE SET
                        scan_time = EXCLUDED.scan_time,
                        scan_session_id = EXCLUDED.scan_session_id,
                        section_title = EXCLUDED.section_title,
                        section_url = EXCLUDED.section_url,
                        section_index = EXCLUDED.section_index,
                        item_title = EXCLUDED.item_title,
                        item_url = EXCLUDED.item_url,
                        item_index = EXCLUDED.item_index,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                """),
                {
                    'trade_date': trade_date,
                    'scan_time': scan_time,
                    'scan_session_id': scan_session_id,
                    'section_key': record.get('section_key'),
                    'section_title': record.get('section_title'),
                    'section_url': record.get('section_url'),
                    'section_index': record.get('section_index'),
                    'item_key': record.get('item_key'),
                    'item_title': record.get('item_title'),
                    'item_url': record.get('item_url'),
                    'item_index': record.get('item_index'),
                    'record_key': record_key,
                    'title': record.get('title'),
                    'summary': record.get('summary'),
                    'raw_json': json.dumps(record, ensure_ascii=False, default=str),
                },
            )
        for record in content_records:
            row_key = str(record.get('row_key') or '').strip()
            if not row_key:
                row_key = _stable_directory_record_key(
                    'content',
                    trade_date,
                    record,
                    ['section_key', 'item_key', 'page_url', 'page_title', 'table_index', 'row_index', 'code', 'title', 'summary'],
                )
            active_db.execute(
                text("""
                    INSERT INTO scan_data_directory_content (
                        trade_date, scan_time, scan_session_id, section_key, section_title, section_url,
                        item_key, item_title, item_url, page_url, page_title, table_index, row_index,
                        row_key, code, title, summary, cells, raw_json
                    ) VALUES (
                        :trade_date, :scan_time, :scan_session_id, :section_key, :section_title, :section_url,
                        :item_key, :item_title, :item_url, :page_url, :page_title, :table_index, :row_index,
                        :row_key, :code, :title, :summary, :cells, :raw_json
                    )
                    ON CONFLICT (trade_date, row_key) DO UPDATE SET
                        scan_time = EXCLUDED.scan_time,
                        scan_session_id = EXCLUDED.scan_session_id,
                        section_title = EXCLUDED.section_title,
                        section_url = EXCLUDED.section_url,
                        item_title = EXCLUDED.item_title,
                        item_url = EXCLUDED.item_url,
                        page_url = EXCLUDED.page_url,
                        page_title = EXCLUDED.page_title,
                        table_index = EXCLUDED.table_index,
                        row_index = EXCLUDED.row_index,
                        code = EXCLUDED.code,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        cells = EXCLUDED.cells,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                """),
                {
                    'trade_date': trade_date,
                    'scan_time': scan_time,
                    'scan_session_id': scan_session_id,
                    'section_key': record.get('section_key'),
                    'section_title': record.get('section_title'),
                    'section_url': record.get('section_url'),
                    'item_key': record.get('item_key'),
                    'item_title': record.get('item_title'),
                    'item_url': record.get('item_url'),
                    'page_url': record.get('page_url'),
                    'page_title': record.get('page_title'),
                    'table_index': record.get('table_index'),
                    'row_index': record.get('row_index'),
                    'row_key': row_key,
                    'code': record.get('code'),
                    'title': record.get('title'),
                    'summary': record.get('summary'),
                    'cells': json.dumps(record.get('cells') or [], ensure_ascii=False, default=str),
                    'raw_json': json.dumps(record, ensure_ascii=False, default=str),
                },
            )


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description='xiaogu DB utilities')
    ap.add_argument('command', choices=['init', 'status'], help='init: create tables; status: check connection')
    ap.add_argument('--sql', default='scripts/xiaogu_db_init.sql', help='path to init SQL file')
    args = ap.parse_args()
    if args.command == 'init':
        init_db(args.sql)
        print('DB tables created OK')
    elif args.command == 'status':
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        print(f'DB connection OK: {DATABASE_URL.split("@")[-1]}')


if __name__ == '__main__':
    main()
