"""Main orchestrator for the Gatto Farioli local intelligence system.

Session 1 intentionally does one useful thing: initialize the SQLite database,
load config.yaml, ingest tier_1 RSS news, and prove dedupe works on repeat runs.
Later sessions will add prices, macro, prediction markets, analysis, alerts, and
brief generation without changing this simple entrypoint shape.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from storage.db import DEFAULT_DB_PATH, get_conn, init_db, query_one

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"


def record_run(module: str, status: str, message: str, started_at: datetime, db_path: str | Path) -> None:
    """Record a module health row so future --health output has source status."""
    finished_at = datetime.now(timezone.utc)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs (module, status, message, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(module) DO UPDATE SET
                status=excluded.status,
                message=excluded.message,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at
            """,
            (module, status, message, started_at.isoformat(), finished_at.isoformat()),
        )


def print_health(db_path: str | Path) -> None:
    """Print latest module health and a few row counts for quick local diagnosis."""
    init_db(db_path)
    counts = {
        "news": query_one("SELECT COUNT(*) AS n FROM news", db_path=db_path)["n"],
        "prices": query_one("SELECT COUNT(*) AS n FROM prices", db_path=db_path)["n"],
        "macro": query_one("SELECT COUNT(*) AS n FROM macro", db_path=db_path)["n"],
        "prediction_markets": query_one("SELECT COUNT(*) AS n FROM prediction_markets", db_path=db_path)["n"],
        "alerts": query_one("SELECT COUNT(*) AS n FROM alerts", db_path=db_path)["n"],
        "briefs": query_one("SELECT COUNT(*) AS n FROM briefs", db_path=db_path)["n"],
    }
    print("Gatto Farioli health")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT module, status, message, finished_at FROM runs ORDER BY module").fetchall()
    if rows:
        print("Latest runs:")
        for row in rows:
            print(f"  {row['module']}: {row['status']} at {row['finished_at']} — {row['message']}")


async def run_ingestion(args: argparse.Namespace) -> int:
    """Run Session 1 ingestion and return a process exit code."""
    db_path = Path(args.db)
    init_db(db_path)
    started = datetime.now(timezone.utc)
    try:
        from config import load_config
        from ingestion.news import ingest_tier_1_news

        config = load_config(args.config)
        result = await ingest_tier_1_news(config, db_path=str(db_path), dry_run=args.dry_run)
        message = (
            f"feeds {result.feeds_succeeded}/{result.feeds_attempted}, "
            f"parsed {result.parsed_entries}, inserted {result.inserted}, "
            f"duplicates {result.duplicates}, failures {len(result.failed_feeds)}"
        )
        status = "ok" if result.feeds_succeeded else "error"
        record_run("news", status, message, started, db_path)
        print(f"Gatto Farioli news ingestion: {message}")
        for failure in result.failed_feeds:
            print(f"  WARN source failed: {failure}")
        return 0 if result.feeds_succeeded else 1
    except ModuleNotFoundError as exc:
        message = f"Missing Python dependency: {exc.name}. From gatto_farioli/, run `pip install -r requirements.txt`."
        record_run("news", "error", message, started, db_path)
        print(f"ERROR: {message}")
        return 1
    except Exception as exc:
        record_run("news", "error", str(exc), started, db_path)
        raise


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for local/manual runs and future schedulers."""
    parser = argparse.ArgumentParser(description="Run Gatto Farioli local intelligence jobs")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse sources without writing rows")
    parser.add_argument("--health", action="store_true", help="Show local database/module health and exit")
    return parser.parse_args()


def main() -> int:
    """Entrypoint used by humans now and cron/launchd in later sessions."""
    args = parse_args()
    if args.health:
        print_health(Path(args.db))
        return 0
    return asyncio.run(run_ingestion(args))


if __name__ == "__main__":
    raise SystemExit(main())
