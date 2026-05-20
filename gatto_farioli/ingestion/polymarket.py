"""Polymarket prediction market snapshots and universe discovery.

Uses Polymarket's public Gamma API (read-only, no auth) for configured
position snapshots and open-market discovery. Mirrors the Kalshi ingestion
shape in ``ingestion/kalshi.py``.
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

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com/markets"
USER_AGENT = "GattoFarioli/0.1 macro intelligence (Polymarket Gamma public API)"
SOURCE_HEALTH_DISCOVERY = "polymarket:gamma:markets"
PURGE_AFTER_DAYS = 30


@dataclass
class PolymarketIngestionResult:
    """Structured summary of one Polymarket snapshot run."""

    markets_attempted: int
    markets_succeeded: int
    snapshots_inserted: int
    failures: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PolymarketDiscoveryResult:
    """Structured summary of one Polymarket market-universe discovery run."""

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
    if f != f:
        return None
    return f


def _parse_outcome_prices(market: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (yes_price, no_price) from Gamma outcomePrices or bid/ask."""
    raw = market.get("outcomePrices")
    prices: list[Any] = []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                prices = parsed
        except json.JSONDecodeError:
            prices = []
    elif isinstance(raw, list):
        prices = raw

    yes_p = _safe_float(prices[0]) if len(prices) > 0 else None
    no_p = _safe_float(prices[1]) if len(prices) > 1 else None
    if yes_p is None:
        yes_p = _safe_float(market.get("bestBid"))
    if no_p is None and yes_p is not None:
        no_p = round(1.0 - yes_p, 4)
    if no_p is None:
        no_p = _safe_float(market.get("bestAsk"))
    return yes_p, no_p


def _market_condition_id(market: dict[str, Any]) -> str | None:
    cid = market.get("conditionId") or market.get("condition_id")
    return str(cid).strip() if cid else None


def _fetch_market_by_condition(
    client: httpx.Client,
    condition_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one market by condition id via Gamma query param."""
    try:
        response = client.get(
            POLYMARKET_GAMMA_BASE,
            params={"condition_id": condition_id},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        return None, f"network error: {exc}"

    if response.status_code == 404:
        return None, "404 — market not found at Polymarket Gamma endpoint"
    if response.status_code in (401, 403):
        return None, f"{response.status_code} — auth required for this market"
    if response.status_code >= 400:
        return None, f"{response.status_code} {response.reason_phrase}"

    try:
        payload = response.json()
    except ValueError as exc:
        return None, f"non-JSON response: {exc}"

    if isinstance(payload, list):
        if not payload:
            return None, "empty market list"
        first = payload[0]
        return (first, None) if isinstance(first, dict) else (None, "invalid market object")
    if isinstance(payload, dict):
        return payload, None
    return None, "response missing market object"


def _snapshot_row(cfg_position: dict[str, Any], market: dict[str, Any], snapshot_at: str) -> dict[str, Any] | None:
    """Build a row dict for the ``prediction_markets`` table."""
    condition_id = _market_condition_id(market) or cfg_position.get("ticker")
    if not condition_id:
        return None
    yes_p, no_p = _parse_outcome_prices(market)
    title = market.get("question") or market.get("title")
    end_raw = market.get("endDate") or market.get("endDateIso") or market.get("end_date")
    resolves_at = end_raw[:10] if isinstance(end_raw, str) and end_raw else None
    volume = _safe_float(market.get("volume24hr")) or _safe_float(market.get("volume24hrClob"))
    oi = _safe_float(market.get("openInterest"))

    return {
        "platform": "polymarket",
        "ticker": condition_id,
        "title": title,
        "yes_price": yes_p,
        "no_price": no_p,
        "volume_24h": volume,
        "open_interest": oi,
        "resolves_at": resolves_at,
        "snapshot_at": snapshot_at,
    }


def ingest_polymarket_markets(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> PolymarketIngestionResult:
    """Fetch a fresh snapshot for every configured Polymarket market."""
    from storage import source_health

    raw_markets = (config.get("portfolio", {}) or {}).get("prediction_markets", []) or []
    markets = [
        m for m in raw_markets
        if (m.get("platform") or "").lower() == "polymarket" and m.get("ticker")
    ]

    if not markets:
        return PolymarketIngestionResult(0, 0, 0, [])

    failures: list[dict[str, str]] = []
    snapshots: list[dict[str, Any]] = []
    snapshot_at = datetime.now(timezone.utc).isoformat()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        for cfg in markets:
            condition_id = cfg["ticker"]
            source_key = f"polymarket:gamma:{condition_id}"
            market, error = _fetch_market_by_condition(client, condition_id)
            if error or market is None:
                failures.append({"ticker": condition_id, "error": error or "empty response"})
                logger.warning("[polymarket] %s — %s", condition_id, error or "empty response")
                if not dry_run:
                    source_health.record_failure(source_key, error or "empty response", db_path=db_path)
                continue
            row = _snapshot_row(cfg, market, snapshot_at)
            if row is None:
                failures.append({"ticker": condition_id, "error": "could not build snapshot row"})
                if not dry_run:
                    source_health.record_failure(source_key, "could not build snapshot row", db_path=db_path)
                continue
            snapshots.append(row)
            if not dry_run:
                source_health.record_success(source_key, "gamma ok", db_path=db_path)

    if dry_run:
        return PolymarketIngestionResult(
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

    return PolymarketIngestionResult(
        markets_attempted=len(markets),
        markets_succeeded=len(snapshots),
        snapshots_inserted=inserted,
        failures=failures,
    )


# ── Canonical category taxonomy (same buckets as Kalshi) ───────────────────
CANONICAL_CATEGORIES = (
    "macro", "rates", "inflation", "commodities", "energy", "geopolitics",
    "weather", "politics", "economics", "sports", "crypto", "other",
)

_POLYMARKET_CATEGORY_BASE = {
    "politics": "politics",
    "elections": "politics",
    "economics": "economics",
    "finance": "macro",
    "financials": "macro",
    "macro": "macro",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
    "sports": "sports",
    "weather": "weather",
    "climate": "weather",
    "science": "other",
    "tech": "other",
    "entertainment": "other",
    "culture": "other",
}

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
    (("nfl", "nba", "mlb", "nhl", "super bowl", "world cup", "champions league"),
     "sports"),
)


def _word_or_phrase_in(needle: str, haystack: str) -> bool:
    if " " in needle or "-" in needle:
        return needle in haystack
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def _event_haystack(event: dict[str, Any]) -> str:
    """Build lowercase text from market + nested Gamma event metadata."""
    parts: list[str] = []
    for key in ("question", "title", "description", "groupItemTitle"):
        val = event.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    tags = event.get("tags")
    if isinstance(tags, str):
        parts.append(tags)
    elif isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    nested = event.get("events") or []
    if isinstance(nested, list) and nested:
        first = nested[0]
        if isinstance(first, dict):
            for key in ("title", "description", "ticker", "slug"):
                val = first.get(key)
                if isinstance(val, str) and val:
                    parts.append(val)
    return " ".join(parts).lower()


def categorize_polymarket_event(event: dict[str, Any]) -> str:
    """Return the canonical category for a Polymarket market/event dict."""
    raw_cat = (event.get("category") or "").strip().lower()
    if not raw_cat and event.get("tags"):
        tag0 = event.get("tags")
        if isinstance(tag0, list) and tag0:
            raw_cat = str(tag0[0]).strip().lower()
        elif isinstance(tag0, str):
            raw_cat = tag0.strip().lower()
    base = _POLYMARKET_CATEGORY_BASE.get(raw_cat, "other")

    haystack = _event_haystack(event)
    for needles, override in _TITLE_CATEGORY_OVERRIDES:
        if any(_word_or_phrase_in(n, haystack) for n in needles):
            return override
    return base


def filter_polymarket_events(
    events: list[dict[str, Any]],
    *,
    include_categories: list[str],
    exclude_categories: list[str],
) -> list[tuple[dict[str, Any], str]]:
    """Categorize and filter markets against include/exclude lists."""
    inc = {c.strip().lower() for c in (include_categories or []) if c}
    exc = {c.strip().lower() for c in (exclude_categories or []) if c}
    out: list[tuple[dict[str, Any], str]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        cat = categorize_polymarket_event(ev)
        if cat in exc:
            continue
        if inc and cat not in inc:
            continue
        out.append((ev, cat))
    return out


def _to_canonical_market_row(
    event: dict[str, Any],
    canonical_category: str,
    discovered_at: str,
    updated_at: str,
) -> dict[str, Any] | None:
    """Build a row for the ``market_universe`` table."""
    symbol = _market_condition_id(event)
    if not symbol:
        return None
    yes_p, no_p = _parse_outcome_prices(event)
    title = event.get("question") or event.get("title")
    closes_at = event.get("endDate") or event.get("endDateIso")
    volume = _safe_float(event.get("volume24hr")) or _safe_float(event.get("volume24hrClob")) or 0.0
    oi = _safe_float(event.get("openInterest"))
    liquidity = _safe_float(event.get("liquidityNum")) or _safe_float(event.get("liquidity")) or volume
    last_price = _safe_float(event.get("lastTradePrice"))
    nested = event.get("events") or []
    event_meta = nested[0] if isinstance(nested, list) and nested and isinstance(nested[0], dict) else {}
    metadata = {
        "slug": event.get("slug"),
        "raw_category": event.get("category"),
        "group_item_title": event.get("groupItemTitle"),
        "event_slug": event_meta.get("slug"),
        "event_ticker": event_meta.get("ticker"),
        "active": event.get("active"),
        "closed": event.get("closed"),
    }
    return {
        "platform": "polymarket",
        "symbol": symbol,
        "title": title,
        "category": canonical_category,
        "status": "open",
        "liquidity": liquidity,
        "volume_24h": volume,
        "open_interest": oi,
        "last_price": last_price,
        "yes_price": yes_p,
        "no_price": no_p,
        "closes_at": closes_at,
        "discovered_at": discovered_at,
        "updated_at": updated_at,
        "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
    }


def _to_snapshot_row(market: dict[str, Any], snapshot_at: str) -> dict[str, Any] | None:
    """Build a row for the ``prediction_markets`` time-series table."""
    condition_id = _market_condition_id(market)
    if not condition_id:
        return None
    yes_p, no_p = _parse_outcome_prices(market)
    title = market.get("question") or market.get("title")
    end_raw = market.get("endDate") or market.get("endDateIso")
    resolves_at = end_raw[:10] if isinstance(end_raw, str) and end_raw else None
    return {
        "platform": "polymarket",
        "ticker": condition_id,
        "title": title,
        "yes_price": yes_p,
        "no_price": no_p,
        "volume_24h": _safe_float(market.get("volume24hr")) or _safe_float(market.get("volume24hrClob")),
        "open_interest": _safe_float(market.get("openInterest")),
        "resolves_at": resolves_at,
        "snapshot_at": snapshot_at,
    }


def discover_polymarket_universe(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
    page_limit: int = 100,
) -> PolymarketDiscoveryResult:
    """Page Polymarket Gamma open markets, categorize, filter, persist."""
    from storage import source_health

    poly_cfg = (config.get("polymarket") or {}) if isinstance(config, dict) else {}
    include_categories = list(poly_cfg.get("include_categories") or [])
    exclude_categories = list(poly_cfg.get("exclude_categories") or ["sports"])
    min_volume = float(poly_cfg.get("min_volume_24h") or 0)
    min_oi = float(poly_cfg.get("min_open_interest") or 0)
    max_markets = int(poly_cfg.get("max_markets_per_run") or 500)

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
    seen_symbols: set[str] = set()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        offset = 0
        while len(universe_rows) < max_markets:
            params: dict[str, Any] = {
                "closed": "false",
                "limit": page_limit,
                "offset": offset,
            }
            try:
                response = client.get(POLYMARKET_GAMMA_BASE, params=params)
            except httpx.HTTPError as exc:
                failures.append({"endpoint": "markets", "error": f"network: {exc}"})
                break
            if response.status_code in (401, 403):
                failures.append({
                    "endpoint": "markets",
                    "error": f"{response.status_code} — Gamma markets endpoint requires auth",
                })
                break
            if response.status_code != 200:
                failures.append({
                    "endpoint": "markets",
                    "error": f"{response.status_code} {response.reason_phrase}",
                })
                break
            try:
                payload = response.json()
            except ValueError as exc:
                failures.append({"endpoint": "markets", "error": f"non-JSON response: {exc}"})
                break

            if not isinstance(payload, list):
                failures.append({"endpoint": "markets", "error": "expected JSON array"})
                break

            pages_fetched += 1
            if not payload:
                break

            events_scanned += len(payload)
            for market in payload:
                markets_scanned += 1
                if not isinstance(market, dict):
                    continue
                if market.get("closed") is True or market.get("active") is False:
                    continue

            for ev, canonical in filter_polymarket_events(
                payload,
                include_categories=include_categories,
                exclude_categories=exclude_categories,
            ):
                events_matched += 1
                symbol = _market_condition_id(ev)
                if not symbol or symbol in seen_symbols:
                    continue

                volume = (
                    _safe_float(ev.get("volume24hr"))
                    or _safe_float(ev.get("volume24hrClob"))
                    or 0.0
                )
                oi = _safe_float(ev.get("openInterest")) or 0.0
                if volume < min_volume:
                    continue
                if oi < min_oi:
                    continue

                universe_row = _to_canonical_market_row(
                    ev, canonical, discovered_at=now_iso, updated_at=now_iso,
                )
                snap_row = _to_snapshot_row(ev, snapshot_at)
                if universe_row is None:
                    continue
                seen_symbols.add(symbol)
                universe_rows.append(universe_row)
                if snap_row is not None:
                    snapshot_rows.append(snap_row)
                by_category[canonical] += 1
                if len(universe_rows) >= max_markets:
                    break

            if len(universe_rows) >= max_markets or len(payload) < page_limit:
                break
            offset += page_limit

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
                "DELETE FROM market_universe WHERE platform = 'polymarket' AND updated_at < ?",
                (purge_cutoff,),
            )
            universe_purged = cur.rowcount or 0

    if failures:
        source_health.record_failure(
            SOURCE_HEALTH_DISCOVERY,
            failures[-1].get("error", "unknown failure"),
            db_path=db_path,
        )
    elif events_scanned > 0:
        source_health.record_success(
            SOURCE_HEALTH_DISCOVERY,
            f"markets {events_scanned}, matched {events_matched}, universe {len(universe_rows)}",
            db_path=db_path,
        )

    return PolymarketDiscoveryResult(
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


__all__ = [
    "POLYMARKET_GAMMA_BASE",
    "CANONICAL_CATEGORIES",
    "PolymarketIngestionResult",
    "PolymarketDiscoveryResult",
    "categorize_polymarket_event",
    "filter_polymarket_events",
    "ingest_polymarket_markets",
    "discover_polymarket_universe",
]
