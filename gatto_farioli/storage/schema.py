"""SQLite schema definitions for Gatto Farioli.

The schema intentionally mirrors the build specification so Daniel can inspect
one file and understand what persistent state the system owns. Session 1 creates
all tables up front even though later sessions populate most of them.
"""

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY,
    url_hash TEXT UNIQUE,
    url TEXT,
    source TEXT,
    title TEXT,
    summary TEXT,
    full_text TEXT,
    sectors TEXT,
    sentiment REAL,
    importance REAL,
    published_at TIMESTAMP,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_importance ON news(importance DESC);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT,
    date DATE,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    pct_change REAL,
    pct_change_5d REAL,
    pct_change_30d REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS macro (
    indicator TEXT,
    date DATE,
    value REAL,
    PRIMARY KEY (indicator, date)
);

CREATE TABLE IF NOT EXISTS prediction_markets (
    platform TEXT,
    ticker TEXT,
    title TEXT,
    yes_price REAL,
    no_price REAL,
    volume_24h REAL,
    open_interest REAL,
    resolves_at DATE,
    snapshot_at TIMESTAMP,
    PRIMARY KEY (platform, ticker, snapshot_at)
);

CREATE TABLE IF NOT EXISTS portwatch (
    date DATE PRIMARY KEY,
    ma_7day REAL,
    daily_calls INTEGER,
    daily_volume_tons REAL,
    pulled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    shares REAL,
    avg_cost REAL,
    current_price REAL,
    market_value REAL,
    unrealized_pnl REAL,
    thesis TEXT,
    conviction INTEGER,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS theses (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT,
    status TEXT,
    confidence INTEGER,
    confirming_signals TEXT,
    breaking_signals TEXT,
    last_reviewed TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    severity TEXT,
    category TEXT,
    message TEXT,
    related_ticker TEXT,
    related_thesis TEXT,
    suggested_action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    user_response TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    type TEXT,
    content TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    module TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    message TEXT,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL
);

-- Rolling narrative memory across days/weeks/months.
-- One row per story cluster. cluster_key is a deterministic hash of the
-- cluster's top distinctive terms + sectors. Article membership is recomputed
-- on every build_narrative_clusters() run (we do not store a junction table
-- yet); the cluster signature (top_terms list) is packed into `summary` as
-- JSON so the matcher can recover it without a schema change.
CREATE TABLE IF NOT EXISTS narrative_clusters (
    id INTEGER PRIMARY KEY,
    cluster_key TEXT UNIQUE,
    title TEXT,
    summary TEXT,
    sectors TEXT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    article_count INTEGER,
    avg_importance REAL,
    max_importance REAL,
    momentum_24h REAL,
    momentum_7d REAL,
    status TEXT,
    related_tickers TEXT,
    related_markets TEXT,
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_narrative_last_seen ON narrative_clusters(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_narrative_status ON narrative_clusters(status);

-- Scored opportunity memory (Phase D). One row per stable candidate_key; reruns
-- refresh score/action/last_seen without duplicating.
CREATE TABLE IF NOT EXISTS opportunity_candidates (
    id INTEGER PRIMARY KEY,
    candidate_key TEXT UNIQUE NOT NULL,
    title TEXT,
    summary TEXT,
    source_type TEXT,
    related_ticker TEXT,
    related_market_ticker TEXT,
    related_narrative_id INTEGER,
    score REAL,
    confidence REAL,
    action TEXT,
    signals_count INTEGER,
    missing_data TEXT,
    evidence TEXT,
    created_at TIMESTAMP,
    last_seen TIMESTAMP,
    status TEXT DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_opportunity_action ON opportunity_candidates(action);
CREATE INDEX IF NOT EXISTS idx_opportunity_score ON opportunity_candidates(score DESC);
CREATE INDEX IF NOT EXISTS idx_opportunity_last_seen ON opportunity_candidates(last_seen DESC);

-- Current-state view of every discovered Kalshi / Polymarket / equity-watch
-- market. prediction_markets stays as the time-series snapshot table;
-- market_universe is "what is open right now, categorized".
CREATE TABLE IF NOT EXISTS market_universe (
    platform TEXT,
    symbol TEXT,
    title TEXT,
    category TEXT,
    status TEXT,
    liquidity REAL,
    volume_24h REAL,
    open_interest REAL,
    last_price REAL,
    yes_price REAL,
    no_price REAL,
    closes_at TIMESTAMP,
    discovered_at TIMESTAMP,
    updated_at TIMESTAMP,
    metadata TEXT,
    PRIMARY KEY (platform, symbol)
);

CREATE INDEX IF NOT EXISTS idx_universe_category ON market_universe(category);
CREATE INDEX IF NOT EXISTS idx_universe_updated ON market_universe(updated_at DESC);

-- Per-source health record. Aggregate, not per-failure-log. Lets the radar
-- and --health surface 'this RSS feed has been failing for 3 days' without
-- scanning every runs row.
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    status TEXT,
    last_success TIMESTAMP,
    last_failure TIMESTAMP,
    failure_count INTEGER DEFAULT 0,
    message TEXT
);
"""
