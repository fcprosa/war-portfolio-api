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
"""
