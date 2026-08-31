CREATE TABLE IF NOT EXISTS production_runs (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    payload JSONB NOT NULL,
    source TEXT,
    source_time TEXT,
    symbol TEXT,
    payload_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_lineage_id ON snapshots (lineage_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_date_symbol ON snapshots (trade_date, symbol);
CREATE TABLE IF NOT EXISTS picks (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    position_state TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE picks ADD COLUMN IF NOT EXISTS decision_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS paper_observations (
    paper_signal_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_time TIMESTAMPTZ NOT NULL,
    reference_price DOUBLE PRECISION NOT NULL,
    paper_observation_state TEXT NOT NULL,
    paper_position_state TEXT NOT NULL,
    alpha_name TEXT NOT NULL,
    alpha_version TEXT,
    feature_version TEXT,
    decision_version TEXT NOT NULL,
    cost_model_version TEXT NOT NULL,
    paper_observation_contract_version TEXT NOT NULL,
    paper_only BOOLEAN NOT NULL DEFAULT TRUE,
    live_order BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (decision_id),
    FOREIGN KEY (decision_id) REFERENCES picks (decision_id),
    CHECK (paper_only),
    CHECK (NOT live_order)
);
CREATE INDEX IF NOT EXISTS idx_paper_observations_signal_time
    ON paper_observations(signal_time);
CREATE TABLE IF NOT EXISTS trading_calendar (
    trade_date DATE PRIMARY KEY,
    market TEXT NOT NULL DEFAULT 'ASHARE',
    is_trading_day BOOLEAN NOT NULL,
    source TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calendar_version TEXT NOT NULL DEFAULT 'CN_A_SHARE_2026_V1',
    payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trading_calendar_open_days
    ON trading_calendar(trade_date) WHERE is_trading_day;
CREATE TABLE IF NOT EXISTS trading_calendar_migrations (
    id BIGSERIAL PRIMARY KEY,
    migration_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    market TEXT NOT NULL,
    previous_is_trading_day BOOLEAN,
    previous_source TEXT,
    previous_calendar_version TEXT,
    new_is_trading_day BOOLEAN NOT NULL,
    new_source TEXT NOT NULL,
    new_calendar_version TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS returns (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    decision_id TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS ledger (
    id BIGSERIAL PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS model_registry (
    model_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS alpha_health (
    id BIGSERIAL PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Historical validation data is separate from live decisions, while returns
-- remain the single outcome table for both forward and replay records.
CREATE TABLE IF NOT EXISTS canonical_historical_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    signal_time TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ NOT NULL,
    snapshot_version TEXT NOT NULL,
    point_in_time BOOLEAN NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    price_basis TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (point_in_time),
    CHECK (available_at <= signal_time)
);
CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_lineage_id
    ON canonical_historical_snapshots(lineage_id);
CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_date
    ON canonical_historical_snapshots(trade_date, symbol);

CREATE TABLE IF NOT EXISTS canonical_future_prices (
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION,
    amount DOUBLE PRECISION,
    source TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ,
    price_basis TEXT NOT NULL,
    price_fact_hash TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date),
    CHECK (price_basis = 'UNADJUSTED')
);
CREATE INDEX IF NOT EXISTS idx_canonical_future_prices_date
    ON canonical_future_prices(date, symbol);

ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source_time TEXT;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS symbol TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS decision_id TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS state TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS position_state TEXT;
ALTER TABLE picks ADD COLUMN IF NOT EXISTS payload JSONB;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS decision_id TEXT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS payload JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_returns_decision_id ON returns (decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_returns_decision_date ON returns (decision_id, trade_date) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_lineage_id ON snapshots (lineage_id);
