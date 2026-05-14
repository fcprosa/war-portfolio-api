"""Rule-based delta detection over the local SQLite store.

``compute_delta`` answers the brief's first real question — *what changed?*
— from data already in the database. No LLM, no network calls, no
fabrication.

Returned shape::

    {
        "window_hours": int,
        "cutoff_iso": str,
        "important_news": [news rows above importance floor in the window],
        "portfolio_full": [every position with latest price joined in],
        "portfolio_movers": [positions with |pct_change|>=1% or |5d|>=5%],
        "watchlist_movers": [watchlist tickers with |pct_change|>=2% or |5d|>=5%],
        "prediction_markets_latest": [latest snapshot per (platform,ticker)],
        "missing_data": [{"category": str, "detail": str}, ...],
        "summary": {counts},
    }
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_conn

DEFAULT_IMPORTANCE_FLOOR = 4.0
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MAX_NEWS = 25
PORTFOLIO_MOVER_PCT_1D = 1.0
PORTFOLIO_MOVER_PCT_5D = 5.0
WATCHLIST_MOVER_PCT_1D = 2.0
WATCHLIST_MOVER_PCT_5D = 5.0


def _is_mover(pct1: float | None, pct5: float | None, threshold_1d: float, threshold_5d: float) -> bool:
    if pct1 is not None and abs(pct1) >= threshold_1d:
        return True
    if pct5 is not None and abs(pct5) >= threshold_5d:
        return True
    return False


def compute_delta(
    hours_back: int = DEFAULT_WINDOW_HOURS,
    config: dict[str, Any] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    max_news: int = DEFAULT_MAX_NEWS,
) -> dict[str, Any]:
    """Return a structured summary of meaningful changes in the last ``hours_back`` hours."""

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    with get_conn(db_path) as conn:
        news_rows = conn.execute(
            """
            SELECT id, url, source, title, importance, sectors, published_at, ingested_at
            FROM news
            WHERE COALESCE(importance, 0) >= ?
              AND COALESCE(published_at, ingested_at) >= ?
            ORDER BY importance DESC, COALESCE(published_at, ingested_at) DESC
            LIMIT ?
            """,
            (importance_floor, cutoff_iso, max_news),
        ).fetchall()

        positions_rows = conn.execute(
            """
            SELECT p.ticker, p.shares, p.avg_cost, p.current_price, p.market_value,
                   p.unrealized_pnl, p.thesis, p.conviction, p.last_updated,
                   pr.date AS price_date, pr.close, pr.pct_change, pr.pct_change_5d, pr.pct_change_30d
            FROM positions p
            LEFT JOIN prices pr
              ON pr.ticker = p.ticker
             AND pr.date = (SELECT MAX(date) FROM prices WHERE ticker = p.ticker)
            ORDER BY p.ticker
            """
        ).fetchall()

        watchlist_tickers: list[str] = []
        if config:
            for syms in (config.get("watchlist", {}) or {}).values():
                for s in syms or []:
                    if s and s not in watchlist_tickers:
                        watchlist_tickers.append(s)

        watchlist_rows: list[dict[str, Any]] = []
        for ticker in watchlist_tickers:
            row = conn.execute(
                """
                SELECT ticker, date, close, pct_change, pct_change_5d, pct_change_30d, volume
                FROM prices
                WHERE ticker = ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
            if row:
                watchlist_rows.append(dict(row))

        pm_latest_rows = conn.execute(
            """
            SELECT pm.platform, pm.ticker, pm.title, pm.yes_price, pm.no_price,
                   pm.volume_24h, pm.open_interest, pm.resolves_at, pm.snapshot_at
            FROM prediction_markets pm
            JOIN (
                SELECT platform, ticker, MAX(snapshot_at) AS max_snap
                FROM prediction_markets
                GROUP BY platform, ticker
            ) latest
              ON latest.platform = pm.platform
             AND latest.ticker = pm.ticker
             AND latest.max_snap = pm.snapshot_at
            ORDER BY pm.platform, pm.ticker
            """
        ).fetchall()

        runs_rows = conn.execute(
            "SELECT module, status, message, finished_at FROM runs"
        ).fetchall()

    portfolio_full = [dict(r) for r in positions_rows]
    portfolio_movers = [
        r for r in portfolio_full
        if _is_mover(r.get("pct_change"), r.get("pct_change_5d"), PORTFOLIO_MOVER_PCT_1D, PORTFOLIO_MOVER_PCT_5D)
    ]

    watchlist_movers = [
        r for r in watchlist_rows
        if _is_mover(r.get("pct_change"), r.get("pct_change_5d"), WATCHLIST_MOVER_PCT_1D, WATCHLIST_MOVER_PCT_5D)
    ]
    watchlist_movers.sort(key=lambda r: abs(r.get("pct_change") or 0.0), reverse=True)
    watchlist_movers = watchlist_movers[:15]

    missing_data: list[dict[str, str]] = []
    pm_keys = {(r["platform"], r["ticker"]) for r in pm_latest_rows}
    if config:
        for pm_cfg in (config.get("portfolio", {}) or {}).get("prediction_markets", []) or []:
            ticker = pm_cfg.get("ticker")
            platform = (pm_cfg.get("platform") or "").lower()
            if ticker and (platform, ticker) not in pm_keys:
                missing_data.append({
                    "category": "prediction_market",
                    "detail": f"{platform}:{ticker} — no live snapshot in DB (configured position will be priced as unavailable)",
                })

    for r in runs_rows:
        if r["status"] == "error":
            missing_data.append({
                "category": "ingestion",
                "detail": f"{r['module']} — {r['message']}",
            })

    for r in portfolio_full:
        if r.get("close") is None and r.get("current_price") is None:
            missing_data.append({
                "category": "price",
                "detail": f"{r['ticker']} — no latest close in prices",
            })

    summary = {
        "news_in_window": len(news_rows),
        "portfolio_movers_count": len(portfolio_movers),
        "watchlist_movers_count": len(watchlist_movers),
        "prediction_market_snapshots": len(pm_latest_rows),
        "missing_signals_count": len(missing_data),
    }

    return {
        "window_hours": hours_back,
        "cutoff_iso": cutoff_iso,
        "important_news": [dict(r) for r in news_rows],
        "portfolio_full": portfolio_full,
        "portfolio_movers": portfolio_movers,
        "watchlist_movers": watchlist_movers,
        "prediction_markets_latest": [dict(r) for r in pm_latest_rows],
        "missing_data": missing_data,
        "summary": summary,
    }


__all__ = ["compute_delta", "DEFAULT_IMPORTANCE_FLOOR", "DEFAULT_WINDOW_HOURS"]
