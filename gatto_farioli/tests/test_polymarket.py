"""Phase F Polymarket ingestion tests — no network."""

from __future__ import annotations

import json
import httpx

from ingestion.polymarket import (
    SOURCE_HEALTH_DISCOVERY,
    PolymarketDiscoveryResult,
    PolymarketIngestionResult,
    _snapshot_row,
    _to_canonical_market_row,
    _to_snapshot_row,
    categorize_polymarket_event,
    discover_polymarket_universe,
    filter_polymarket_events,
    ingest_polymarket_markets,
)
from storage.db import get_conn, init_db, query_one
from storage.source_health import list_unhealthy


def test_categorize_polymarket_event_known_buckets() -> None:
    cases = [
        ({"category": "Politics", "question": "2026 senate race"}, "politics"),
        ({"question": "Bitcoin price by year end"}, "crypto"),
        ({"category": "Sports", "question": "MLB game outcome"}, "sports"),
        ({"question": "Will unemployment fall below 4% in 2026"}, "economics"),
        ({"category": "Health", "question": "Random health story"}, "other"),
    ]
    for event, expected in cases:
        got = categorize_polymarket_event(event)
        assert got == expected, f"categorize({event}) = {got!r}, expected {expected!r}"


def test_filter_polymarket_events_excludes_sports_by_default(minimal_config) -> None:
    from config import load_config
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    poly_cfg = cfg.get("polymarket") or {}
    inc = poly_cfg.get("include_categories") or []
    exc = poly_cfg.get("exclude_categories") or []
    assert "sports" in exc

    events = [
        {"category": "Sports", "question": "MLB game outcome"},
        {"category": "Politics", "question": "Senate race"},
        {"category": "Weather", "question": "Hurricane season count"},
    ]
    filtered = filter_polymarket_events(events, include_categories=inc, exclude_categories=exc)
    cats = [c for _, c in filtered]
    assert "sports" not in cats
    assert "politics" in cats and "weather" in cats


def test_snapshot_row_matches_prediction_markets_schema() -> None:
    market = {
        "conditionId": "0xabc123",
        "question": "Will CPI exceed 3%?",
        "outcomePrices": '["0.42", "0.58"]',
        "volume24hr": 12000.5,
        "openInterest": 900.0,
        "endDate": "2026-06-30T00:00:00Z",
    }
    row = _snapshot_row({"ticker": "0xabc123"}, market, "2026-05-20T12:00:00+00:00")
    assert row is not None
    assert row["platform"] == "polymarket"
    assert row["ticker"] == "0xabc123"
    assert row["title"] == "Will CPI exceed 3%?"
    assert row["yes_price"] == 0.42
    assert row["no_price"] == 0.58
    assert row["volume_24h"] == 12000.5
    assert row["open_interest"] == 900.0
    assert row["resolves_at"] == "2026-06-30"
    assert row["snapshot_at"] == "2026-05-20T12:00:00+00:00"


def test_universe_row_matches_market_universe_schema() -> None:
    market = {
        "conditionId": "0xdef456",
        "question": "Fed cut in June?",
        "outcomePrices": '["0.33", "0.67"]',
        "volume24hr": 5000,
        "liquidityNum": 800,
        "lastTradePrice": 0.34,
        "endDate": "2026-07-01T00:00:00Z",
        "events": [{"slug": "fed-june", "title": "Fed June decision"}],
    }
    row = _to_canonical_market_row(
        market, "rates", discovered_at="2026-05-20T12:00:00+00:00", updated_at="2026-05-20T12:00:00+00:00",
    )
    assert row is not None
    assert row["platform"] == "polymarket"
    assert row["symbol"] == "0xdef456"
    assert row["category"] == "rates"
    assert row["status"] == "open"
    assert row["yes_price"] == 0.33
    assert row["no_price"] == 0.67
    meta = json.loads(row["metadata"])
    assert meta["slug"] is not None or meta.get("event_slug")


def test_discovery_http_failure_records_source_health(tmp_db, minimal_config, monkeypatch) -> None:
    init_db(tmp_db)

    class RaisingClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("ingestion.polymarket.httpx.Client", lambda **kwargs: RaisingClient())

    result = discover_polymarket_universe(minimal_config, db_path=tmp_db)
    assert isinstance(result, PolymarketDiscoveryResult)
    assert result.failures
    assert result.markets_matched == 0

    unhealthy = list_unhealthy(db_path=tmp_db)
    sources = {r["source"] for r in unhealthy}
    assert SOURCE_HEALTH_DISCOVERY in sources


def test_zero_configured_positions_writes_no_snapshots(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    result = ingest_polymarket_markets(minimal_config, db_path=tmp_db)
    assert isinstance(result, PolymarketIngestionResult)
    assert result.markets_attempted == 0
    assert result.snapshots_inserted == 0
    n = query_one(
        "SELECT COUNT(*) AS n FROM prediction_markets WHERE platform = 'polymarket'",
        db_path=tmp_db,
    )["n"]
    assert n == 0


def test_to_snapshot_row_from_synthetic_market() -> None:
    """Discovery helper row builder (used in universe discovery path)."""
    market = {
        "conditionId": "0x999",
        "question": "Oil above $90?",
        "outcomePrices": ["0.55", "0.45"],
        "volume24hr": 100,
        "endDateIso": "2026-08-01",
    }
    row = _to_snapshot_row(market, "2026-05-20T15:00:00+00:00")
    assert row is not None
    assert row["platform"] == "polymarket"
    assert row["ticker"] == "0x999"
