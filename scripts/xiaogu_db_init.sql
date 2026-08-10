CREATE EXTENSION IF NOT EXISTS vector;

-- picks: 每日出票记录（替代 jsonl ledger）
CREATE TABLE IF NOT EXISTS picks (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10),
    decision VARCHAR(20) NOT NULL,
    final_score FLOAT,
    blockers JSONB DEFAULT '[]',
    features JSONB DEFAULT '{}',
    source_layers JSONB DEFAULT '[]',
    rule_version VARCHAR(50),
    scan_dir VARCHAR(500),
    dry_run BOOLEAN DEFAULT TRUE,
    paper_only BOOLEAN DEFAULT TRUE,
    no_trade BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64)
);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS stock_name VARCHAR(30);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS rank INT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS structured_score FLOAT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS ranking_basis JSONB DEFAULT '{}';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS ticket_reason JSONB DEFAULT '{}';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS selection_reason JSONB DEFAULT '{}';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS paper_pick_eligibility JSONB DEFAULT '{}';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS official_target_exclusion_reasons JSONB DEFAULT '[]';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS risk_flags JSONB DEFAULT '[]';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS auxiliary_evidence_status VARCHAR(20);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS information_coverage_audit_snapshot JSONB DEFAULT '{}';
ALTER TABLE picks ADD COLUMN IF NOT EXISTS source_summary_path VARCHAR(500);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS formal_rank_snapshot_id VARCHAR(128);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS formal_rank_snapshot_version VARCHAR(128);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS scoring_config_hash VARCHAR(128);
CREATE INDEX IF NOT EXISTS idx_picks_production_run ON picks(production_run_id, trade_date, decision);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'picks'::regclass
          AND conname = 'uq_picks_trade_date_symbol_decision'
    ) THEN
        ALTER TABLE picks DROP CONSTRAINT uq_picks_trade_date_symbol_decision;
    END IF;
END $$;
DROP INDEX IF EXISTS idx_picks_unique_pick;
CREATE UNIQUE INDEX IF NOT EXISTS uq_picks_legacy_trade_date_symbol_decision
    ON picks(trade_date, symbol, decision) WHERE production_run_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_picks_production_run_symbol_decision
    ON picks(production_run_id, symbol, decision) WHERE production_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_picks_trade_date ON picks(trade_date);
CREATE INDEX IF NOT EXISTS idx_picks_symbol ON picks(symbol);
CREATE INDEX IF NOT EXISTS idx_picks_decision ON picks(decision);

-- scoring_config: tunable closed-loop parameters
CREATE TABLE IF NOT EXISTS scoring_config (
    id SERIAL PRIMARY KEY,
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    data_version VARCHAR(64)
);

INSERT INTO scoring_config (config_key, config_value, description)
VALUES
    ('weekday_blocklist', '', 'Blocked weekdays: empty=none, 0=Monday'),
    ('max_score_cap', '88', 'Cap for score penalty'),
    ('follow_on_strategy', 't1_close_primary', 'Follow-on strategy selection'),
    ('follow_on_t1_weight', '1.0', 'Follow-on T+1 weight'),
    ('follow_on_t2_weight', '0.45', 'Follow-on T+2 weight'),
    ('follow_on_t3_weight', '0.25', 'Follow-on T+3 weight'),
    ('follow_on_limit_up_threshold', '0.095', 'Follow-on winner threshold'),
    ('horizon_aware_strategy', 'instant_then_delayed', 'Lifecycle-aware pick strategy'),
    ('instant_momentum_min_confirmations', '2', 'Minimum instant confirmation signals'),
    ('delayed_setup_min_persistence', '2', 'Minimum repeat/persistence count'),
    ('delayed_setup_floor_score', '75', 'Minimum score for delayed setup review'),
    ('delayed_setup_theme_min_score', '0.5', 'Theme score floor for delayed setup'),
    ('stale_repeat_window_days', '5', 'Window for repeated stale candidates'),
    ('stale_decay_factor', '0.65', 'Decay factor for stale repeated setups'),
    ('l2_limit_strength_bonus', '100', 'L2 tier multiplier in priority tuple'),
    ('sector_catalyst_penalty', '100', 'Pure sector/momentum penalty multiplier'),
    ('near_limit_l2_exemption', 'true', 'Exempt near_limit_up_risk when L2 confirmed'),
    ('concept_flow_bonus_threshold_high', '50', 'Concept flow >=50亿 gets +4 bonus'),
    ('concept_flow_bonus_threshold_mid', '20', 'Concept flow >=20亿 gets +2 bonus'),
    ('concept_flow_bonus_threshold_low', '5', 'Concept flow >=5亿 gets +1 bonus'),
    ('concept_flow_penalty_threshold', '-10', 'Concept flow <=-10亿 gets -2 penalty')
ON CONFLICT (config_key) DO UPDATE SET
    config_value = EXCLUDED.config_value,
    description = EXCLUDED.description,
    updated_at = NOW();

-- returns: T+1/2/3 收益回填
CREATE TABLE IF NOT EXISTS returns (
    id SERIAL PRIMARY KEY,
    pick_id INT REFERENCES picks(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    t1_return FLOAT,
    t2_return FLOAT,
    t3_return FLOAT,
    t5_return FLOAT,
    is_limit_up BOOLEAN GENERATED ALWAYS AS (t1_return >= 0.095) STORED,
    filled_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(trade_date, symbol)
);
ALTER TABLE returns ADD COLUMN IF NOT EXISTS t5_return FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS t1_return_close FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS t1_return_high FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS t1_vwap FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS next_day_open_return FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS next_day_high_return FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS next_day_low_return FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS next_day_gap_return FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS next_day_drawdown FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS high_to_close_retrace FLOAT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);
ALTER TABLE returns ADD COLUMN IF NOT EXISTS candidate_snapshot_id VARCHAR(128);
ALTER TABLE returns ADD COLUMN IF NOT EXISTS return_status VARCHAR(20) DEFAULT 'PENDING';
ALTER TABLE returns ADD COLUMN IF NOT EXISTS settlement_evidence JSONB DEFAULT '{}';
ALTER TABLE returns ADD COLUMN IF NOT EXISTS correction_of_id INT REFERENCES returns(id);
CREATE INDEX IF NOT EXISTS idx_returns_production_run ON returns(production_run_id, trade_date, symbol);
CREATE INDEX IF NOT EXISTS idx_returns_candidate_snapshot ON returns(candidate_snapshot_id, symbol);
DO $$
DECLARE constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'returns'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) LIKE 'UNIQUE (trade_date, symbol)%'
    LIMIT 1;
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE returns DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_returns_legacy_trade_date_symbol
    ON returns(trade_date, symbol) WHERE production_run_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_returns_production_run_symbol
    ON returns(production_run_id, symbol) WHERE production_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_returns_trade_date ON returns(trade_date);
CREATE INDEX IF NOT EXISTS idx_returns_symbol ON returns(symbol);

-- scan_sessions: 扫描会话元数据
CREATE TABLE IF NOT EXISTS scan_sessions (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    scan_time TIMESTAMPTZ NOT NULL,
    source_id VARCHAR(100),
    quotes_count INT DEFAULT 0,
    scored_count INT DEFAULT 0,
    passed_count INT DEFAULT 0,
    scan_dir VARCHAR(500),
    status VARCHAR(20) DEFAULT 'completed',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64)
);

ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS market_snapshot JSONB DEFAULT '{}';
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS source_status JSONB DEFAULT '{}';
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS source_counts JSONB DEFAULT '{}';
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS source_diagnostics JSONB DEFAULT '{}';
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);

-- Immutable production run lineage. Legacy rows remain queryable with NULL run ids.
CREATE TABLE IF NOT EXISTS production_runs (
    production_run_id VARCHAR(128) PRIMARY KEY,
    trade_date DATE NOT NULL,
    scan_session_id INT REFERENCES scan_sessions(id),
    run_mode VARCHAR(40) NOT NULL DEFAULT 'LIVE_DAILY_PIPELINE',
    rule_version VARCHAR(50),
    runner_version VARCHAR(100),
    scanner_version VARCHAR(100),
    schema_version VARCHAR(100),
    scoring_config_snapshot JSONB DEFAULT '{}',
    scoring_config_hash VARCHAR(128),
    input_payload_hash VARCHAR(128),
    status VARCHAR(40) NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    retry_command TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_production_runs_trade_date ON production_runs(trade_date, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_production_runs_status ON production_runs(status);

CREATE TABLE IF NOT EXISTS production_run_steps (
    production_run_id VARCHAR(128) NOT NULL REFERENCES production_runs(production_run_id) ON DELETE CASCADE,
    step_name VARCHAR(80) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    required BOOLEAN NOT NULL DEFAULT TRUE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    retry_command TEXT,
    metadata JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (production_run_id, step_name)
);
CREATE INDEX IF NOT EXISTS idx_production_run_steps_status ON production_run_steps(production_run_id, status, required);

CREATE TABLE IF NOT EXISTS production_run_active (
    trade_date DATE PRIMARY KEY,
    production_run_id VARCHAR(128) NOT NULL REFERENCES production_runs(production_run_id),
    candidate_snapshot_id VARCHAR(128),
    active_pick_id INT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_production_run_active_run ON production_run_active(production_run_id);

-- scan_market_data: one raw payload per source domain for each scan session.
-- This is the DB replay source; JSONL files are an operational export only.
CREATE TABLE IF NOT EXISTS scan_market_data (
    id SERIAL PRIMARY KEY,
    scan_session_id INT NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    scan_time TIMESTAMPTZ NOT NULL,
    domain VARCHAR(100) NOT NULL,
    item_count INT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '[]',
    source_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(scan_session_id, domain)
);

CREATE INDEX IF NOT EXISTS idx_scan_market_data_date_domain ON scan_market_data(trade_date, domain);

-- signal_effectiveness: 每日信号分析快照
CREATE TABLE IF NOT EXISTS signal_effectiveness (
    id SERIAL PRIMARY KEY,
    analysis_date DATE NOT NULL,
    signal_key VARCHAR(50) NOT NULL,
    present_count INT DEFAULT 0,
    limit_up_rate FLOAT DEFAULT 0.0,
    avg_t1_return FLOAT DEFAULT 0.0,
    weight_suggestion VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(analysis_date, signal_key)
);

-- signals: 每只股票的原始信号快照
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    signal_key VARCHAR(50) NOT NULL,
    signal_value FLOAT,
    raw_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(trade_date, symbol, signal_key)
);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);
DO $$
DECLARE constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'signals'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) LIKE 'UNIQUE (trade_date, symbol, signal_key)%'
    LIMIT 1;
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE signals DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_legacy_trade_date_symbol_key
    ON signals(trade_date, symbol, signal_key) WHERE production_run_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_production_run_symbol_key
    ON signals(production_run_id, symbol, signal_key) WHERE production_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_key ON signals(signal_key);
CREATE INDEX IF NOT EXISTS idx_signals_production_run ON signals(production_run_id, trade_date, symbol);

-- research_runs: 研究/扫描运行版本信息
CREATE TABLE IF NOT EXISTS research_runs (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    run_type VARCHAR(30) NOT NULL,
    run_time TIMESTAMPTZ,
    rule_version VARCHAR(50),
    source_id VARCHAR(100),
    scanner_version VARCHAR(50),
    runner_version VARCHAR(50),
    quotes_count INT DEFAULT 0,
    candidates_count INT DEFAULT 0,
    passed_count INT DEFAULT 0,
    run_dir VARCHAR(500),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_research_runs_date ON research_runs(trade_date);
CREATE INDEX IF NOT EXISTS idx_research_runs_type ON research_runs(run_type);

-- daily_candidates: 每日候选个股数据与选股逻辑
CREATE TABLE IF NOT EXISTS daily_candidates (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    stock_name VARCHAR(30),
    rank INT,
    final_score FLOAT,
    is_official_pick BOOLEAN DEFAULT FALSE,
    decision VARCHAR(20),
    open_price FLOAT,
    close_price FLOAT,
    high_price FLOAT,
    low_price FLOAT,
    volume BIGINT,
    amount FLOAT,
    pct_chg FLOAT,
    turnover_rate FLOAT,
    sentiment_catalyst TEXT,
    theme_catalyst TEXT,
    news_catalyst TEXT,
    positive_catalyst TEXT,
    selection_reason TEXT,
    selection_outcome VARCHAR(50),
    selection_outcome_reason TEXT,
    signal_pct FLOAT,
    close_position_score FLOAT,
    fund_flow_momentum FLOAT,
    sector_catalyst_score FLOAT,
    early_opportunity_score FLOAT,
    topic_propagation_score FLOAT,
    market_regime VARCHAR(20),
    blockers JSONB DEFAULT '[]',
    hard_gate_status JSONB DEFAULT '{}',
    eligibility_snapshot JSONB DEFAULT '{}',
    selection_diagnostics JSONB DEFAULT '{}',
    raw_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(trade_date, symbol)
);
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS selection_outcome VARCHAR(50);
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS selection_outcome_reason TEXT;
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS eligibility_snapshot JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS selection_diagnostics JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS source_layers JSONB DEFAULT '[]';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS candidate_features JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS candidate_entry_reason JSONB DEFAULT '[]';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS ticket_reason JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS not_selected_reason JSONB DEFAULT '[]';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS factor_snapshot JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS auxiliary_evidence_snapshot JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS ranking_basis JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS postmortem_snapshot JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS future_return_fields_placeholder JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS cohort VARCHAR(40);
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS cohort_quality VARCHAR(40);
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS cohort_status_flags JSONB DEFAULT '[]';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS reconstruction_provenance JSONB DEFAULT '{}';
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS production_run_id VARCHAR(128);
ALTER TABLE daily_candidates ADD COLUMN IF NOT EXISTS candidate_snapshot_id VARCHAR(128);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'daily_candidates'::regclass
          AND conname = 'daily_candidates_trade_date_symbol_key'
    ) THEN
        ALTER TABLE daily_candidates DROP CONSTRAINT daily_candidates_trade_date_symbol_key;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_candidates_legacy_trade_date_symbol
    ON daily_candidates(trade_date, symbol) WHERE production_run_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_candidates_production_run_symbol
    ON daily_candidates(production_run_id, symbol) WHERE production_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dc_production_run ON daily_candidates(production_run_id, trade_date, rank);
CREATE INDEX IF NOT EXISTS idx_dc_snapshot ON daily_candidates(candidate_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_dc_date ON daily_candidates(trade_date);
CREATE INDEX IF NOT EXISTS idx_dc_symbol ON daily_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_dc_official ON daily_candidates(trade_date, is_official_pick);

-- scan_data_directory_catalog: 行情中心目录页清单
CREATE TABLE IF NOT EXISTS scan_data_directory_catalog (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    scan_time TIMESTAMPTZ,
    scan_session_id INT REFERENCES scan_sessions(id) ON DELETE CASCADE,
    section_key VARCHAR(100),
    section_title VARCHAR(200),
    section_url VARCHAR(500),
    section_index INT,
    item_key VARCHAR(100) NOT NULL,
    item_title VARCHAR(200),
    item_url VARCHAR(500),
    item_index INT,
    record_key VARCHAR(200) NOT NULL,
    title VARCHAR(300),
    summary TEXT,
    raw_json JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(trade_date, record_key)
);

CREATE INDEX IF NOT EXISTS idx_scan_dir_catalog_date ON scan_data_directory_catalog(trade_date);
CREATE INDEX IF NOT EXISTS idx_scan_dir_catalog_item ON scan_data_directory_catalog(item_key);

-- scan_data_directory_content: 行情中心目录页内容数据
CREATE TABLE IF NOT EXISTS scan_data_directory_content (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    scan_time TIMESTAMPTZ,
    scan_session_id INT REFERENCES scan_sessions(id) ON DELETE CASCADE,
    section_key VARCHAR(100),
    section_title VARCHAR(200),
    section_url VARCHAR(500),
    item_key VARCHAR(100),
    item_title VARCHAR(200),
    item_url VARCHAR(500),
    page_url VARCHAR(500),
    page_title VARCHAR(300),
    table_index INT,
    row_index INT,
    row_key VARCHAR(200) NOT NULL,
    code VARCHAR(20),
    title VARCHAR(300),
    summary TEXT,
    cells JSONB DEFAULT '[]',
    raw_json JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    data_version VARCHAR(64),
    UNIQUE(trade_date, row_key)
);

CREATE INDEX IF NOT EXISTS idx_scan_dir_content_date ON scan_data_directory_content(trade_date);
CREATE INDEX IF NOT EXISTS idx_scan_dir_content_code ON scan_data_directory_content(code);
CREATE INDEX IF NOT EXISTS idx_scan_dir_content_item ON scan_data_directory_content(item_key);
