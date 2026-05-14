"""Kalshi prediction market snapshots.

Hits Kalshi's public market endpoint and stores one snapshot row per
configured prediction market in ``prediction_markets``. We rely on the
same endpoint the war-portfolio-api JS layer has already proven (see
``lib/kalshi.js``), so no new endpoints are invented here.

If the API returns 404 / 401 / network failure we record a clean failure
in the returned summary; the snapshot row is simply skipped. ``positions``
and ``prediction_markets`` configured in config.yaml stay visible to the
brief layer even when live pricing is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from storage.db import DEFAULT_DB_PATH, get_conn

logger = logging.getLogger(__name__)

KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2/markets"
USER_AGENT = "GattoFarioli/0.1 macro intelligence (Kalshi public market snapshot)"


@dataclass
class KalshiIngestionResult:
    """Structured summary of one Kalshi ingestion run."""

    markets_attempted: int
    markets_succeeded: int
    snapshots_inserted: int
    failures: list[dict[str, str]] = field(default_factory=list)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _mid_or_ask(market: dict[str, Any], side: str) -> float | None:
    """Return the live mark for `side` — mid of bid/ask, else ask, else bid."""
    ask = _safe_float(market.get(f"{side}_ask_dollars"))
    bid = _safe_float(market.get(f"{side}_bid_dollars"))
    if ask is not None and bid is not None:
        return round((ask + bid) / 2.0, 4)
    if ask is not None:
        return round(ask, 4)
    if bid is not None:
        return round(bid, 4)
    return None


def _fetch_market(client: httpx.Client, ticker: str) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one market or return a clean error string."""
    try:
        response = client.get(f"{KALSHI_API_BASE}/{ticker}", timeout=10.0)
    except httpx.HTTPError as exc:
        return None, f"network error: {exc}"

    if response.status_code == 404:
        return None, "404 — market not found at Kalshi public endpoint"
    if response.status_code in (401, 403):
        return None, f"{response.status_code} — auth required for this market"
    if response.status_code >= 400:
        return None, f"{response.status_code} {response.reason_phrase}"

    try:
        payload = response.json()
    except ValueError as exc:
        return None, f"non-JSON response: {exc}"

    market = payload.get("market") if isinstance(payload, dict) else None
    if isinstance(market, dict):
        return market, None
    if isinstance(payload, dict) and "ticker" in payload:
        return payload, None
    return None, "response missing market object"


def _snapshot_row(cfg_position: dict[str, Any], market: dict[str, Any], snapshot_at: str) -> dict[str, Any]:
    """Build a row dict for the ``prediction_markets`` table."""
    title = market.get("title") or market.get("subtitle") or market.get("event_ticker")
    resolves_at_raw = market.get("close_time") or market.get("expiration_time")
    resolves_at = None
    if isinstance(resolves_at_raw, str) and resolves_at_raw:
        resolves_at = resolves_at_raw[:10]

    return {
        "platform": "kalshi",
        "ticker": cfg_position["ticker"],
        "title": title,
        "yes_price": _mid_or_ask(market, "yes"),
        "no_price": _mid_or_ask(market, "no"),
        "volume_24h": _safe_float(market.get("volume_24h_fp")) or _safe_float(market.get("volume_24h")),
        "open_interest": _safe_float(market.get("open_interest")),
        "resolves_at": resolves_at,
        "snapshot_at": snapshot_at,
    }


def ingest_kalshi_markets(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> KalshiIngestionResult:
    """Fetch a fresh snapshot for every configured Kalshi market."""
    raw_markets = (config.get("portfolio", {}) or {}).get("prediction_markets", []) or []
    markets = [m for m in raw_markets if (m.get("platform") or "").lower() == "kalshi" and m.get("ticker")]

    if not markets:
        return KalshiIngestionResult(0, 0, 0, [])

    failures: list[dict[str, str]] = []
    snapshots: list[dict[str, Any]] = []
    snapshot_at = datetime.now(timezone.utc).isoformat()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        for cfg in markets:
            ticker = cfg["ticker"]
            market, error = _fetch_market(client, ticker)
            if error or market is None:
                failures.append({"ticker": ticker, "error": error or "empty response"})
                logger.warning("[kalshi] %s — %s", ticker, error or "empty response")
                continue
            snapshots.append(_snapshot_row(cfg, market, snapshot_at))

    if dry_run:
        return KalshiIngestionResult(
            markets_attempted=len(markets),
            markets_succeeded=len(snapshots),
            snapshots_inserted=len(snapshots),
            failures=failures,
        )

    inserted = 0
    with get_conn(db_path) as conn:
        for row in snapshots:
            conn.execute(
                """
                INSERT INTO prediction_markets (
                    platform, ticker, title, yes_price, no_price,
                    volume_24h, open_interest, resolves_at, snapshot_at
                )
                VALUES (
                    :platform, :ticker, :title, :yes_price, :no_price,
                    :volume_24h, :open_interest, :resolves_at, :snapshot_at
                )
                ON CONFLICT(platform, ticker, snapshot_at) DO UPDATE SET
                    title=excluded.title,
                    yes_price=excluded.yes_price,
                    no_price=excluded.no_price,
                    volume_24h=excluded.volume_24h,
                    open_interest=excluded.open_interest,
                    resolves_at=excluded.resolves_at
                """,
                row,
            )
            inserted += 1

    return KalshiIngestionResult(
        markets_attempted=len(markets),
        markets_succeeded=len(snapshots),
        snapshots_inserted=inserted,
        failures=failures,
    )


__all__ = ["KALSHI_API_BASE", "KalshiIngestionResult", "ingest_kalshi_markets"]
