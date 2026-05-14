"""Main orchestrator for the Gatto Farioli local intelligence system.

Flags:
  --health        Print module health and row counts, then exit.
  --ingest        Run every ingestion source plus position sync (default).
  --dry-run       Fetch and compute, but do not write any DB rows.
  --no-ingest     Skip ingestion (reserved for future --brief flows).
  --config PATH   Path to config.yaml.
  --db PATH       Path to SQLite DB (defaults to ./argos.db).

Every ingestion module is wrapped in try/except so a failing source records
``error`` in ``runs`` and the rest of the pipeline still proceeds.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
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
        "positions": query_one("SELECT COUNT(*) AS n FROM positions", db_path=db_path)["n"],
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


async def _run_news(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Run tier-1 news ingestion. Returns (status, message)."""
    from ingestion.news import ingest_tier_1_news

    result = await ingest_tier_1_news(config, db_path=str(args.db), dry_run=args.dry_run)
    message = (
        f"feeds {result.feeds_succeeded}/{result.feeds_attempted}, "
        f"parsed {result.parsed_entries}, inserted {result.inserted}, "
        f"duplicates {result.duplicates}, failures {len(result.failed_feeds)}"
    )
    status = "ok" if result.feeds_succeeded else "error"
    for failure in result.failed_feeds:
        print(f"  WARN news source failed: {failure}")
    return status, message


def _run_prices(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Run yfinance price ingestion."""
    from ingestion.prices import ingest_prices

    result = ingest_prices(config, db_path=args.db, dry_run=args.dry_run)
    message = (
        f"tickers {result.tickers_succeeded}/{result.tickers_attempted}, "
        f"rows {result.rows_upserted}, failures {len(result.failures)}"
    )
    status = "ok" if result.tickers_succeeded else "error"
    for f in result.failures:
        print(f"  WARN prices ticker failed: {f['ticker']} — {f['error']}")
    return status, message


def _run_kalshi(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Run Kalshi public market snapshot."""
    from ingestion.kalshi import ingest_kalshi_markets

    result = ingest_kalshi_markets(config, db_path=args.db, dry_run=args.dry_run)
    if result.markets_attempted == 0:
        return "skipped", "no kalshi markets configured"
    message = (
        f"markets {result.markets_succeeded}/{result.markets_attempted}, "
        f"snapshots {result.snapshots_inserted}, failures {len(result.failures)}"
    )
    status = "ok" if result.markets_succeeded else "error"
    for f in result.failures:
        print(f"  WARN kalshi market failed: {f['ticker']} — {f['error']}")
    return status, message


def _run_state_sync(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Sync configured positions into the positions table."""
    from storage.state import sync_positions

    result = sync_positions(config, db_path=args.db, dry_run=args.dry_run)
    if result.positions_attempted == 0:
        return "skipped", "no positions configured"
    message = (
        f"positions {result.positions_synced}/{result.positions_attempted}, "
        f"priced {result.priced}, unpriced {len(result.unpriced)}"
    )
    if result.unpriced:
        print(f"  WARN positions without latest price: {', '.join(result.unpriced)}")
    return "ok", message


async def run_ingestion(args: argparse.Namespace) -> int:
    """Run every ingestion module and the position sync."""
    db_path = Path(args.db)
    init_db(db_path)

    from config import load_config

    config = load_config(args.config)

    overall_ok = True

    modules: list[tuple[str, callable, bool]] = [
        ("news", _run_news, True),
        ("prices", _run_prices, False),
        ("kalshi", _run_kalshi, False),
        ("state_sync", _run_state_sync, False),
    ]

    for module_name, runner, is_async in modules:
        started = datetime.now(timezone.utc)
        try:
            if is_async:
                status, message = await runner(config, args)
            else:
                status, message = runner(config, args)
        except ModuleNotFoundError as exc:
            status = "error"
            message = f"missing dependency: {exc.name}. Run `pip install -r requirements.txt`."
        except Exception as exc:  # noqa: BLE001 — top-level safety net
            status = "error"
            message = f"unhandled: {exc}"
        record_run(module_name, status, message, started, db_path)
        print(f"Gatto Farioli {module_name}: {status} — {message}")
        if status == "error":
            overall_ok = False

    return 0 if overall_ok else 1


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for local/manual runs and future schedulers."""
    parser = argparse.ArgumentParser(description="Run Gatto Farioli local intelligence jobs")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and compute without writing rows")
    parser.add_argument("--health", action="store_true", help="Show local DB/module health and exit")
    parser.add_argument("--ingest", action="store_true", help="Run news + prices + kalshi + state sync (default)")
    parser.add_argument("--no-ingest", action="store_true", help="Skip ingestion (reserved for future --brief flows)")
    return parser.parse_args()


def main() -> int:
    """Entrypoint used by humans now and cron/launchd in later sessions."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = parse_args()
    if args.health:
        print_health(Path(args.db))
        return 0
    if args.no_ingest:
        print("Gatto Farioli: --no-ingest set and no other action requested; nothing to do.")
        return 0
    return asyncio.run(run_ingestion(args))


if __name__ == "__main__":
    raise SystemExit(main())
