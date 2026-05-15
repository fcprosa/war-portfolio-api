"""SQLite schema and init_db tests."""

from __future__ import annotations

from storage.db import init_db, query_one
from storage.schema import SCHEMA_SQL

# Session 1 core tables plus later-session tables created up front by schema.py.
EXPECTED_TABLES = (
    "news",
    "prices",
    "macro",
    "prediction_markets",
    "portwatch",
    "positions",
    "theses",
    "alerts",
    "briefs",
    "runs",
    "narrative_clusters",
    "opportunity_candidates",
    "market_universe",
    "source_health",
)


def test_schema_sql_defines_expected_tables() -> None:
    for table in EXPECTED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL


def test_init_db_creates_all_tables(tmp_db) -> None:
    init_db(tmp_db)
    for table in EXPECTED_TABLES:
        row = query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
            db_path=tmp_db,
        )
        assert row is not None, f"missing table: {table}"


def test_init_db_is_idempotent(tmp_db) -> None:
    init_db(tmp_db)
    init_db(tmp_db)
    n = query_one(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'",
        db_path=tmp_db,
    )["n"]
    assert n >= len(EXPECTED_TABLES)
