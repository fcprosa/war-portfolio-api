"""Main orchestrator for the Gatto Farioli local intelligence system.

Run from ``gatto_farioli/``::

    python run.py --health

From the repo root::

    python -m gatto_farioli.run --health

Flags:
  --health        Print module health and row counts, then exit.
  --ingest        Run every ingestion source plus scoring + position sync (default).
  --brief         Generate the Daily Edge Brief; runs ingestion first unless --no-ingest.
  --radar         Generate the Daily Radar; runs ingestion first unless --no-ingest.
  --no-ingest     Skip ingestion (use existing DB rows; pairs with --brief or --radar).
  --dry-run       Fetch and compute, but do not write any DB rows.
  --config PATH   Path to config.yaml.
  --db PATH       Path to SQLite DB (defaults to ./argos.db).

Every ingestion module is wrapped in try/except so a failing source records
``error`` in ``runs`` and the rest of the pipeline still proceeds.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

_PKG_DIR = _Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

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
        "narrative_clusters": query_one("SELECT COUNT(*) AS n FROM narrative_clusters", db_path=db_path)["n"],
        "opportunity_candidates": query_one("SELECT COUNT(*) AS n FROM opportunity_candidates", db_path=db_path)["n"],
        "market_universe": query_one("SELECT COUNT(*) AS n FROM market_universe", db_path=db_path)["n"],
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

    from storage.source_health import list_unhealthy

    unhealthy = list_unhealthy(db_path)
    if unhealthy:
        print(f"Unhealthy sources ({len(unhealthy)}):")
        for r in unhealthy[:15]:
            label = (r.get("source") or "")[:80]
            last_fail = (r.get("last_failure") or "")[:19]
            print(
                f"  {label} — status={r['status']} fails={r['failure_count']} "
                f"last_failure={last_fail} :: {(r.get('message') or '')[:120]}"
            )


# ── Module runners ─────────────────────────────────────────────────────────
async def _run_news(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    from ingestion.news import ingest_tier_1_news
    from storage import source_health

    result = await ingest_tier_1_news(config, db_path=str(args.db), dry_run=args.dry_run)
    message = (
        f"feeds {result.feeds_succeeded}/{result.feeds_attempted}, "
        f"parsed {result.parsed_entries}, inserted {result.inserted}, "
        f"duplicates {result.duplicates}, failures {len(result.failed_feeds)}"
    )
    status = "ok" if result.feeds_succeeded else "error"
    succeeded_urls = set(config.get("news_sources", {}).get("tier_1", [])) - {
        f.split(" — ", 1)[0] for f in result.failed_feeds
    }
    for url in succeeded_urls:
        source_health.record_success(url, "rss ok", db_path=args.db)
    for failure in result.failed_feeds:
        url, _, err = failure.partition(" — ")
        source_health.record_failure(url, err or "feed error", db_path=args.db)
        print(f"  WARN news source failed: {failure}")
    return status, message


def _run_news_score(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    from analysis.news_score import score_news

    result = score_news(config, db_path=args.db, dry_run=args.dry_run)
    message = (
        f"scanned {result.rows_scanned}, scored {result.rows_scored}, "
        f"skipped {result.rows_skipped}, "
        f"avg {result.avg_score if result.avg_score is not None else 'n/a'}, "
        f"max {result.max_score if result.max_score is not None else 'n/a'}"
    )
    return "ok", message


def _run_narratives(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Rebuild narrative clusters from the news rolling window."""
    from analysis.narratives import build_narrative_clusters

    result = build_narrative_clusters(config, db_path=args.db, dry_run=args.dry_run)
    by_status = ", ".join(f"{k}={v}" for k, v in sorted(result.by_status.items())) or "-"
    message = (
        f"articles {result.articles_scanned}, clusters {result.clusters_total} "
        f"(created {result.clusters_created}, updated {result.clusters_updated}); status [{by_status}]"
    )
    return "ok", message


def _run_prices(config: dict, args: argparse.Namespace) -> tuple[str, str]:
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
    from ingestion.kalshi import ingest_kalshi_markets
    from storage import source_health

    result = ingest_kalshi_markets(config, db_path=args.db, dry_run=args.dry_run)
    if result.markets_attempted == 0:
        return "skipped", "no kalshi markets configured"
    message = (
        f"markets {result.markets_succeeded}/{result.markets_attempted}, "
        f"snapshots {result.snapshots_inserted}, failures {len(result.failures)}"
    )
    status = "ok" if result.markets_succeeded else "error"
    for f in result.failures:
        source_health.record_failure(
            f"kalshi:{f['ticker']}", f.get("error") or "unknown", db_path=args.db,
        )
        print(f"  WARN kalshi market failed: {f['ticker']} — {f['error']}")
    return status, message


def _run_kalshi_discovery(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Discover the open Kalshi market universe (events → markets → category)."""
    from ingestion.kalshi import discover_market_universe

    result = discover_market_universe(config, db_path=args.db, dry_run=args.dry_run)
    cat_summary = ", ".join(f"{k}={v}" for k, v in sorted(result.by_category.items())) or "-"
    message = (
        f"events {result.events_matched}/{result.events_scanned} on {result.pages_fetched} page(s), "
        f"markets {result.markets_matched}/{result.markets_scanned}, "
        f"universe upserted={result.universe_upserted} purged={result.universe_purged}, "
        f"snapshots {result.snapshots_inserted}, by_cat[{cat_summary}], "
        f"failures {len(result.failures)}"
    )
    if result.failures:
        for f in result.failures:
            print(f"  WARN kalshi_discovery: {f.get('endpoint', '?')} — {f.get('error')}")
        if result.markets_matched == 0:
            return "error", message
    return "ok", message


def _run_opportunities(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    """Score and persist opportunity candidates (Phase D)."""
    from analysis.opportunities import score_opportunities

    result = score_opportunities(config, db_path=args.db, dry_run=args.dry_run)
    by_action = ", ".join(f"{k}={v}" for k, v in sorted(result.by_action.items())) or "-"
    message = (
        f"candidates {result.candidates_scored}, inserted {result.inserted}, "
        f"updated {result.updated}; actions [{by_action}]"
    )
    return "ok", message


def _run_state_sync(config: dict, args: argparse.Namespace) -> tuple[str, str]:
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


async def _run_modules(
    args: argparse.Namespace,
    modules: list[tuple[str, callable, bool]],
) -> int:
    """Run a list of modules, recording each in `runs`. Returns 0 on full success."""
    db_path = Path(args.db)
    init_db(db_path)

    from config import load_config

    config = load_config(args.config)

    overall_ok = True
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
        except Exception as exc:  # noqa: BLE001
            status = "error"
            message = f"unhandled: {exc}"
        record_run(module_name, status, message, started, db_path)
        print(f"Gatto Farioli {module_name}: {status} — {message}")
        if status == "error":
            overall_ok = False

    return 0 if overall_ok else 1


async def run_ingestion(args: argparse.Namespace) -> int:
    """Default ingestion pass — news, scoring, narratives, prices, kalshi, universe, state."""
    modules: list[tuple[str, callable, bool]] = [
        ("news", _run_news, True),
        ("news_score", _run_news_score, False),
        ("narratives", _run_narratives, False),
        ("prices", _run_prices, False),
        ("kalshi", _run_kalshi, False),
        ("kalshi_discovery", _run_kalshi_discovery, False),
        ("opportunities", _run_opportunities, False),
        ("state_sync", _run_state_sync, False),
    ]
    return await _run_modules(args, modules)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gatto Farioli local intelligence jobs")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and compute without writing rows")
    parser.add_argument("--health", action="store_true", help="Show local DB/module health and exit")
    parser.add_argument("--ingest", action="store_true", help="Run full ingestion pipeline")
    parser.add_argument("--brief", action="store_true", help="Generate the Daily Edge Brief v1")
    parser.add_argument("--radar", action="store_true", help="Generate the Daily Radar v1")
    parser.add_argument("--no-ingest", action="store_true", help="Skip ingestion (pairs with --brief or --radar)")
    return parser.parse_args()


def run_brief(args: argparse.Namespace) -> int:
    """Generate and print the Daily Edge Brief (Book Monitor)."""
    db_path = Path(args.db)
    init_db(db_path)

    from analysis.brief import generate_daily_brief
    from config import load_config

    config = load_config(args.config)
    started = datetime.now(timezone.utc)
    try:
        text = generate_daily_brief(config, db_path=db_path, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        record_run("brief", "error", f"unhandled: {exc}", started, db_path)
        print(f"ERROR: brief generation failed: {exc}")
        return 1

    record_run("brief", "ok", "daily_edge_v1 generated", started, db_path)
    print(text)
    return 0


def run_radar(args: argparse.Namespace) -> int:
    """Generate and print the Daily Radar (opportunity / narrative surface)."""
    db_path = Path(args.db)
    init_db(db_path)

    from analysis.radar import generate_daily_radar
    from config import load_config

    config = load_config(args.config)
    started = datetime.now(timezone.utc)
    try:
        text = generate_daily_radar(config, db_path=db_path, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        if not args.dry_run:
            record_run("radar", "error", f"unhandled: {exc}", started, db_path)
        print(f"ERROR: radar generation failed: {exc}")
        return 1

    print(text)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = parse_args()

    if args.health:
        print_health(Path(args.db))
        return 0

    if args.brief:
        if not args.no_ingest:
            rc = asyncio.run(run_ingestion(args))
            if rc != 0:
                print("Gatto Farioli: ingestion reported errors; continuing into brief with degraded data.")
        return run_brief(args)

    if args.radar:
        if not args.no_ingest:
            rc = asyncio.run(run_ingestion(args))
            if rc != 0:
                print("Gatto Farioli: ingestion reported errors; continuing into radar with degraded data.")
        return run_radar(args)

    if args.no_ingest:
        print("Gatto Farioli: --no-ingest set without --brief or --radar; nothing to do.")
        return 0

    return asyncio.run(run_ingestion(args))


if __name__ == "__main__":
    raise SystemExit(main())
