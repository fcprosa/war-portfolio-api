"""Per-source health tracking.

Distinct from ``runs`` (which is per-module aggregate). ``source_health``
tracks individual data sources — an RSS feed URL, a Kalshi endpoint, a
ticker batch — so the radar can call out "BBC feed has failed 3x in a row"
without scanning a log.

Idempotent upserts: on success we update last_success and reset status to
'ok' but never clear failure_count (keep the cumulative scar). On failure
we increment failure_count and set status='error'.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from storage.db import DEFAULT_DB_PATH, get_conn

OK = "ok"
ERROR = "error"
STALE = "stale"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_success(source: str, message: str = "", db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Mark a source as healthy after a successful fetch."""
    if not source:
        return
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO source_health (source, status, last_success, last_failure, failure_count, message)
            VALUES (?, ?, ?, NULL, 0, ?)
            ON CONFLICT(source) DO UPDATE SET
                status = excluded.status,
                last_success = excluded.last_success,
                message = excluded.message
            """,
            (source, OK, _now_iso(), message or ""),
        )


def record_failure(source: str, message: str, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Mark a source as broken; bumps failure_count atomically."""
    if not source:
        return
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO source_health (source, status, last_success, last_failure, failure_count, message)
            VALUES (?, ?, NULL, ?, 1, ?)
            ON CONFLICT(source) DO UPDATE SET
                status = excluded.status,
                last_failure = excluded.last_failure,
                failure_count = source_health.failure_count + 1,
                message = excluded.message
            """,
            (source, ERROR, now, message or ""),
        )


def list_unhealthy(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """Return sources whose most recent state is not 'ok', for use in radar/health."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT source, status, last_success, last_failure, failure_count, message
            FROM source_health
            WHERE status != ? OR last_failure > COALESCE(last_success, '')
            ORDER BY COALESCE(last_failure, '') DESC
            """,
            (OK,),
        ).fetchall()
    return [dict(r) for r in rows]


__all__ = ["OK", "ERROR", "STALE", "record_success", "record_failure", "list_unhealthy"]
