CREATE TABLE IF NOT EXISTS production_runs (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS snapshots (
    lineage_id TEXT PRIMARY KEY,
    trade_date DATE NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS picks (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS returns (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol TEXT NOT NULL,
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
    lineage_id TEXT PRIMARY KEY,
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
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
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
ALTER TABLE picks ADD COLUMN IF NOT EXISTS payload JSONB;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS decision_id TEXT;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS payload JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_returns_decision_id ON returns (decision_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_lineage_id ON snapshots (lineage_id);
