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

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from storage.db import DEFAULT_DB_PATH, get_conn

logger = logging.getLogger(__name__)

# Single-ticker fetch (legacy host, still answers for configured tickers we own).
KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2/markets"

# Public market discovery / listing host. Returns paginated open markets
# without auth — verified empirically against the real API.
KALSHI_LIST_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

USER_AGENT = "GattoFarioli/0.1 macro intelligence (Kalshi public market snapshot)"


@dataclass
class KalshiIngestionResult:
    """Structured summary of one Kalshi ingestion run."""

    markets_attempted: int
    markets_succeeded: int
    snapshots_inserted: int
    failures: list[dict[str, str]] = field(default_factory=list)


@dataclass
class KalshiDiscoveryResult:
    """Structured summary of one Kalshi market-universe discovery run.

    Naming retained for run.py compatibility. The semantics are now broader:
    we pull events (not keyword-matched markets), filter by category, and
    persist both the time-series snapshot and a current-state row in
    `market_universe`.
    """

    events_scanned: int
    events_matched: int
    markets_scanned: int
    markets_matched: int
    snapshots_inserted: int
    universe_upserted: int
    universe_purged: int
    pages_fetched: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
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


# ── Canonical category taxonomy ────────────────────────────────────────────
# Internal canonical categories the rest of the system reasons about. The
# spec lists: macro, rates, inflation, commodities, energy, geopolitics,
# weather, politics, economics, sports, crypto, other.
CANONICAL_CATEGORIES = (
    "macro", "rates", "inflation", "commodities", "energy", "geopolitics",
    "weather", "politics", "economics", "sports", "crypto", "other",
)

# Coarse map from Kalshi's API category string to our canonical set. Title
# heuristics (below) refine these further — e.g. an "Economics" event whose
# title mentions CPI lands as 'inflation' instead of 'economics'.
_KALSHI_CATEGORY_BASE = {
    "politics": "politics",
    "elections": "politics",
    "economics": "economics",
    "financials": "macro",
    "climate and weather": "weather",
    "weather": "weather",
    "sports": "sports",
    "companies": "economics",
    "world": "geopolitics",
    "science and technology": "other",
    "entertainment": "other",
    "social": "other",
    "health": "other",
    "transportation": "other",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
}

# Title keyword → category overrides, applied AFTER the base mapping. Order
# matters: first match wins, so put the most-specific signals first.
# Multi-word phrases (e.g. "rate hike") are matched as literal substrings;
# single words are matched with word boundaries so "gold" can't sneak through
# "Goldman" or "eth" through "Ethan".
_TITLE_CATEGORY_OVERRIDES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bitcoin", "btc", "ethereum", "eth", "cryptocurrency", "dogecoin",
      "solana", "doge"), "crypto"),
    (("cpi", "inflation", "pce"), "inflation"),
    (("fomc", "fed ", "fed funds", "rate hike", "rate cut", "fed decision",
      "interest rate", "10-year yield", "10 year yield", "treasury yield",
      "jerome powell", "chair powell", "fed chair"), "rates"),
    (("oil", "crude", "gasoline", "natural gas", "diesel", "brent", "wti",
      "opec"), "energy"),
    (("gold", "silver", "copper", "wheat", "corn", "soybean", "iron ore"),
     "commodities"),
    (("hurricane", "tornado", "snowfall", "heat wave", "heatwave", "rainfall",
      "temperature", "global warming"), "weather"),
    (("hormuz", "iran", "ukraine", "russia", "china war", "taiwan", "israel",
      "gaza", "north korea", "yemen", "houthi"), "geopolitics"),
    (("election", "primary", "senate", "house race", "president", "presidential"),
     "politics"),
    (("unemployment", "payrolls", "jobs report", "gdp", "recession", "ppi"),
     "economics"),
)


def _word_or_phrase_in(needle: str, haystack: str) -> bool:
    """Word-boundary match for single tokens; substring match for phrases.

    A phrase like "rate hike" already includes spaces so a plain substring
    match is safe — it can't trigger on a partial word. Single tokens like
    "gold" or "eth" must be word-bounded so they don't fire on "Goldman"
    or "Ethan".
    """
    if " " in needle or "-" in needle:
        return needle in haystack
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def categorize_kalshi_event(event: dict[str, Any]) -> str:
    """Return the canonical category for a Kalshi event."""
    raw = (event.get("category") or "").strip().lower()
    base = _KALSHI_CATEGORY_BASE.get(raw, "other")

    title = (event.get("title") or "").lower()
    sub_title = (event.get("sub_title") or "").lower()
    haystack = f"{title} {sub_title}"

    for needles, override in _TITLE_CATEGORY_OVERRIDES:
        if any(_word_or_phrase_in(n, haystack) for n in needles):
            return override

    return base


def filter_kalshi_events(
    events: list[dict[str, Any]],
    *,
    include_categories: list[str],
    exclude_categories: list[str],
) -> list[tuple[dict[str, Any], str]]:
    """Categorize and filter events against include/exclude lists.

    Returned shape: ``[(event, canonical_category), ...]`` for every event
    that passes the filter, preserving Kalshi's order.

    Pure function — no DB writes, no HTTP. Used by both the live discovery
    path and the verify suite so categorization stays testable.
    """
    inc = {c.strip().lower() for c in (include_categories or []) if c}
    exc = {c.strip().lower() for c in (exclude_categories or []) if c}
    out: list[tuple[dict[str, Any], str]] = []
    for ev in events or []:
        cat = categorize_kalshi_event(ev)
        if cat in exc:
            continue
        if inc and cat not in inc:
            continue
        out.append((ev, cat))
    return out


KALSHI_EVENTS_BASE = "https://api.elections.kalshi.com/trade-api/v2/events"
SOURCE_HEALTH_KEY = "kalshi_events"
PURGE_AFTER_DAYS = 30


def _to_canonical_market_row(
    event: dict[str, Any],
    market: dict[str, Any],
    canonical_category: str,
    discovered_at: str,
    updated_at: str,
) -> dict[str, Any] | None:
    """Build a row for the ``market_universe`` table."""
    ticker = market.get("ticker")
    if not ticker:
        return None
    title = market.get("title") or event.get("title") or market.get("subtitle")
    closes_at = market.get("close_time") or market.get("expiration_time")
    volume = _safe_float(market.get("volume_24h_fp")) or _safe_float(market.get("volume_24h"))
    oi = _safe_float(market.get("open_interest_fp")) or _safe_float(market.get("open_interest"))
    metadata = {
        "event_ticker": event.get("event_ticker"),
        "series_ticker": event.get("series_ticker"),
        "raw_category": event.get("category"),
        "sub_title": event.get("sub_title"),
        "market_status": market.get("status"),
        "last_price_dollars": market.get("last_price_dollars"),
    }
    return {
        "platform": "kalshi",
        "symbol": ticker,
        "title": title,
        "category": canonical_category,
        "status": "open",
        "liquidity": (volume or 0.0),
        "volume_24h": volume,
        "open_interest": oi,
        "last_price": _safe_float(market.get("last_price_dollars")),
        "yes_price": _mid_or_ask(market, "yes"),
        "no_price": _mid_or_ask(market, "no"),
        "closes_at": closes_at,
        "discovered_at": discovered_at,
        "updated_at": updated_at,
        "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
    }


def _to_snapshot_row(
    market: dict[str, Any],
    snapshot_at: str,
    event_title: str | None = None,
) -> dict[str, Any] | None:
    """Build a row for the ``prediction_markets`` time-series table."""
    ticker = market.get("ticker")
    if not ticker:
        return None
    title = market.get("title") or event_title or market.get("subtitle")
    resolves_raw = market.get("close_time") or market.get("expiration_time")
    resolves_at = resolves_raw[:10] if isinstance(resolves_raw, str) and resolves_raw else None
    return {
        "platform": "kalshi",
        "ticker": ticker,
        "title": title,
        "yes_price": _mid_or_ask(market, "yes"),
        "no_price": _mid_or_ask(market, "no"),
        "volume_24h": _safe_float(market.get("volume_24h_fp")) or _safe_float(market.get("volume_24h")),
        "open_interest": _safe_float(market.get("open_interest_fp")) or _safe_float(market.get("open_interest")),
        "resolves_at": resolves_at,
        "snapshot_at": snapshot_at,
    }


def discover_market_universe(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
    page_limit: int = 200,
) -> KalshiDiscoveryResult:
    """Pull Kalshi's open events with nested markets, categorize, persist.

    Endpoint (verified, no auth needed for read):
        GET https://api.elections.kalshi.com/trade-api/v2/events
            ?status=open&with_nested_markets=true&limit=200
        cursor pagination via the ``cursor`` field.

    Pipeline:
      1. Paginate up to ``kalshi.max_markets_per_run`` markets across events.
      2. Categorize each event with the deterministic ``categorize_kalshi_event``
         + title-keyword refinement.
      3. Filter by ``kalshi.include_categories`` / ``kalshi.exclude_categories``
         (sports is in exclude by default).
      4. Apply ``kalshi.min_volume_24h`` / ``kalshi.min_open_interest``.
      5. Upsert into ``market_universe`` (current state) AND insert a snapshot
         into ``prediction_markets`` (time series).
      6. Purge ``market_universe`` rows that haven't been re-upserted in 30+
         days (closed/resolved markets fall out naturally).
      7. Record source_health for ``kalshi_events``.
    """
    from storage import source_health

    kalshi_cfg = (config.get("kalshi") or {}) if isinstance(config, dict) else {}
    include_categories = list(kalshi_cfg.get("include_categories") or [])
    exclude_categories = list(kalshi_cfg.get("exclude_categories") or ["sports"])
    min_volume = float(kalshi_cfg.get("min_volume_24h") or 0)
    min_oi = float(kalshi_cfg.get("min_open_interest") or 0)
    max_markets = int(kalshi_cfg.get("max_markets_per_run") or 500)

    now_iso = datetime.now(timezone.utc).isoformat()
    snapshot_at = now_iso

    failures: list[dict[str, str]] = []
    by_category: Counter = Counter()
    events_scanned = 0
    events_matched = 0
    markets_scanned = 0
    universe_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    pages_fetched = 0
    seen_tickers: set[str] = set()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        cursor: str | None = None
        while len(universe_rows) < max_markets:
            params: dict[str, Any] = {
                "status": "open",
                "with_nested_markets": "true",
                "limit": page_limit,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                response = client.get(KALSHI_EVENTS_BASE, params=params)
            except httpx.HTTPError as exc:
                failures.append({"endpoint": "events", "error": f"network: {exc}"})
                break
            if response.status_code in (401, 403):
                failures.append({
                    "endpoint": "events",
                    "error": f"{response.status_code} — public events endpoint requires auth (Kalshi changed policy?)",
                })
                break
            if response.status_code != 200:
                failures.append({
                    "endpoint": "events",
                    "error": f"{response.status_code} {response.reason_phrase}",
                })
                break
            try:
                payload = response.json()
            except ValueError as exc:
                failures.append({"endpoint": "events", "error": f"non-JSON response: {exc}"})
                break

            events = payload.get("events") or []
            events_scanned += len(events)
            pages_fetched += 1

            for ev, canonical in filter_kalshi_events(
                events,
                include_categories=include_categories,
                exclude_categories=exclude_categories,
            ):
                events_matched += 1
                event_markets = ev.get("markets") or []
                for market in event_markets:
                    markets_scanned += 1
                    ticker = market.get("ticker")
                    if not ticker or ticker in seen_tickers:
                        continue

                    volume = _safe_float(market.get("volume_24h_fp")) or _safe_float(market.get("volume_24h")) or 0.0
                    oi = _safe_float(market.get("open_interest_fp")) or _safe_float(market.get("open_interest")) or 0.0
                    if volume < min_volume:
                        continue
                    if oi < min_oi:
                        continue

                    universe_row = _to_canonical_market_row(
                        ev, market, canonical, discovered_at=now_iso, updated_at=now_iso,
                    )
                    snap_row = _to_snapshot_row(market, snapshot_at, event_title=ev.get("title"))
                    if universe_row is None:
                        continue
                    seen_tickers.add(ticker)
                    universe_rows.append(universe_row)
                    if snap_row is not None:
                        snapshot_rows.append(snap_row)
                    by_category[canonical] += 1
                    if len(universe_rows) >= max_markets:
                        break
                if len(universe_rows) >= max_markets:
                    break

            cursor = payload.get("cursor") or None
            if not cursor:
                break

    universe_upserted = 0
    snapshots_inserted = 0
    universe_purged = 0

    if not dry_run:
        with get_conn(db_path) as conn:
            for row in universe_rows:
                conn.execute(
                    """
                    INSERT INTO market_universe (
                        platform, symbol, title, category, status,
                        liquidity, volume_24h, open_interest,
                        last_price, yes_price, no_price,
                        closes_at, discovered_at, updated_at, metadata
                    ) VALUES (
                        :platform, :symbol, :title, :category, :status,
                        :liquidity, :volume_24h, :open_interest,
                        :last_price, :yes_price, :no_price,
                        :closes_at, :discovered_at, :updated_at, :metadata
                    )
                    ON CONFLICT(platform, symbol) DO UPDATE SET
                        title = excluded.title,
                        category = excluded.category,
                        status = excluded.status,
                        liquidity = excluded.liquidity,
                        volume_24h = excluded.volume_24h,
                        open_interest = excluded.open_interest,
                        last_price = excluded.last_price,
                        yes_price = excluded.yes_price,
                        no_price = excluded.no_price,
                        closes_at = excluded.closes_at,
                        updated_at = excluded.updated_at,
                        metadata = excluded.metadata
                    """,
                    row,
                )
                universe_upserted += 1

            for snap in snapshot_rows:
                conn.execute(
                    """
                    INSERT INTO prediction_markets (
                        platform, ticker, title, yes_price, no_price,
                        volume_24h, open_interest, resolves_at, snapshot_at
                    ) VALUES (
                        :platform, :ticker, :title, :yes_price, :no_price,
                        :volume_24h, :open_interest, :resolves_at, :snapshot_at
                    )
                    ON CONFLICT(platform, ticker, snapshot_at) DO UPDATE SET
                        title = excluded.title,
                        yes_price = excluded.yes_price,
                        no_price = excluded.no_price,
                        volume_24h = excluded.volume_24h,
                        open_interest = excluded.open_interest,
                        resolves_at = excluded.resolves_at
                    """,
                    snap,
                )
                snapshots_inserted += 1

            purge_cutoff = (datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)).isoformat()
            cur = conn.execute(
                "DELETE FROM market_universe WHERE platform = 'kalshi' AND updated_at < ?",
                (purge_cutoff,),
            )
            universe_purged = cur.rowcount or 0

    if failures:
        source_health.record_failure(
            SOURCE_HEALTH_KEY,
            failures[-1].get("error", "unknown failure"),
            db_path=db_path,
        )
    elif events_scanned > 0:
        source_health.record_success(
            SOURCE_HEALTH_KEY,
            f"events {events_scanned}, matched {events_matched}, markets {markets_scanned}",
            db_path=db_path,
        )

    return KalshiDiscoveryResult(
        events_scanned=events_scanned,
        events_matched=events_matched,
        markets_scanned=markets_scanned,
        markets_matched=len(universe_rows),
        snapshots_inserted=snapshots_inserted,
        universe_upserted=universe_upserted,
        universe_purged=universe_purged,
        pages_fetched=pages_fetched,
        by_category=dict(by_category),
        failures=failures,
    )


# Backward-compat alias — run.py and existing callers expect this name.
discover_kalshi_markets = discover_market_universe


__all__ = [
    "KALSHI_API_BASE",
    "KALSHI_LIST_BASE",
    "KALSHI_EVENTS_BASE",
    "CANONICAL_CATEGORIES",
    "KalshiIngestionResult",
    "KalshiDiscoveryResult",
    "categorize_kalshi_event",
    "filter_kalshi_events",
    "ingest_kalshi_markets",
    "discover_market_universe",
    "discover_kalshi_markets",
]
