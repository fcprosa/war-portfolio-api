"""Small SQLite helper layer for Gatto Farioli.

This module deliberately avoids clever abstractions. Every helper is a thin,
readable wrapper around sqlite3 so the database remains transparent and easy to
repair with the sqlite CLI if a source breaks.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from storage.schema import SCHEMA_SQL

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_DIR / "argos.db"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with row dictionaries and safe pragmas enabled."""
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_conn(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Yield a connection and always close it after the caller finishes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_OPPORTUNITY_V2_COLUMNS = frozenset({
    "candidate_key", "title", "summary", "source_type", "related_ticker",
    "related_market_ticker", "related_narrative_id", "score", "confidence",
    "action", "signals_count", "missing_data", "evidence", "created_at",
    "last_seen", "status",
})

_OPPORTUNITY_V3_COLUMNS: tuple[tuple[str, str], ...] = (
    ("catalyst_path", "TEXT"),
    ("invalidation_trigger", "TEXT"),
    ("risk_reward_summary", "TEXT"),
    ("quality_bar_passed", "INTEGER"),
    ("quality_bar_missing", "TEXT"),
)


def _migrate_opportunity_candidates(conn: sqlite3.Connection) -> None:
    """Drop legacy opportunity_candidates tables that predate Phase D schema."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_candidates'"
    ).fetchone()
    if not row:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunity_candidates)")}
    if cols and not _OPPORTUNITY_V2_COLUMNS.issubset(cols):
        conn.execute("DROP TABLE IF EXISTS opportunity_candidates")


def _upgrade_opportunity_candidates_to_v3(conn: sqlite3.Connection) -> None:
    """Add Phase G Quality Bar columns to existing opportunity_candidates tables."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_candidates'"
    ).fetchone()
    if not row:
        return
    existing = {r[1] for r in conn.execute("PRAGMA table_info(opportunity_candidates)")}
    for col_name, col_type in _OPPORTUNITY_V3_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE opportunity_candidates ADD COLUMN {col_name} {col_type}"
            )


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create every table and index required by the local intelligence system."""
    with get_conn(db_path) as conn:
        _migrate_opportunity_candidates(conn)
        conn.executescript(SCHEMA_SQL)
        _upgrade_opportunity_candidates_to_v3(conn)


def execute(
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Run one write statement and return the number of affected rows."""
    with get_conn(db_path) as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def query_all(
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[sqlite3.Row]:
    """Run a read query and return all rows as sqlite3.Row objects."""
    with get_conn(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def query_one(
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
    db_path: str | Path = DEFAULT_DB_PATH,
) -> sqlite3.Row | None:
    """Run a read query and return the first row, or None if there is no match."""
    with get_conn(db_path) as conn:
        return conn.execute(sql, params).fetchone()
