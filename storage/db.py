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

DEFAULT_DB_PATH = Path("argos.db")


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


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create every table and index required by the local intelligence system."""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


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
