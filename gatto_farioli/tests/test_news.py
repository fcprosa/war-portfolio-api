"""News ingestion unit tests — URL helpers, parsing, persistence."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.news import (
    entry_to_row,
    ingest_tier_1_news,
    normalize_url,
    upsert_news,
    url_hash,
)
from storage.db import get_conn, init_db, query_one


def test_normalize_url_strips_tracking_params() -> None:
    raw = "https://Example.COM/path?utm_source=x&fbclid=abc&keep=1"
    assert normalize_url(raw) == "https://example.com/path?keep=1"


def test_normalize_url_empty() -> None:
    assert normalize_url("") == ""


def test_url_hash_is_stable_for_tracking_variants() -> None:
    a = "https://example.com/article/1?utm_campaign=test"
    b = "https://example.com/article/1"
    assert url_hash(a) == url_hash(b)


def test_url_hash_differs_for_different_paths() -> None:
    assert url_hash("https://example.com/a") != url_hash("https://example.com/b")


def test_entry_to_row_parses_feed_entry() -> None:
    feed = SimpleNamespace(feed={"title": "Example Wire"})
    entry = {
        "title": "Oil rises",
        "link": "https://example.com/1?utm_source=x",
        "summary": "<p>Crude up.</p>",
        "published": "Wed, 14 May 2026 12:00:00 GMT",
    }
    row = entry_to_row("https://feeds.example.com/rss", feed, entry)
    assert row is not None
    assert row["title"] == "Oil rises"
    assert row["url"] == "https://example.com/1"
    assert row["url_hash"] == url_hash("https://example.com/1")
    assert row["source"] == "Example Wire"
    assert row["full_text"] == "Crude up."
    assert row["summary"] is None
    assert row["published_at"] is not None


def test_entry_to_row_returns_none_without_title_or_link() -> None:
    feed = SimpleNamespace(feed={})
    assert entry_to_row("https://feeds.example.com/rss", feed, {"title": ""}) is None
    assert entry_to_row("https://feeds.example.com/rss", feed, {"link": ""}) is None


def test_upsert_news_inserts_and_dedupes(tmp_db) -> None:
    init_db(tmp_db)
    row = {
        "url_hash": "abc123",
        "url": "https://example.com/1",
        "source": "Test",
        "title": "Story",
        "summary": None,
        "full_text": "Body",
        "sectors": None,
        "sentiment": None,
        "importance": None,
        "published_at": "2026-05-14T12:00:00+00:00",
    }
    inserted, dupes = upsert_news([row], db_path=tmp_db)
    assert inserted == 1 and dupes == 0

    inserted2, dupes2 = upsert_news([row], db_path=tmp_db)
    assert inserted2 == 0 and dupes2 == 1

    count = query_one("SELECT COUNT(*) AS n FROM news", db_path=tmp_db)["n"]
    assert count == 1


def _mock_httpx_client(sample_rss_xml: bytes):
    """Return a patched AsyncClient context manager with a canned RSS body."""
    mock_response = MagicMock()
    mock_response.content = sample_rss_xml
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    return patch("ingestion.news.httpx.AsyncClient", return_value=mock_client)


@pytest.mark.asyncio
async def test_ingest_tier_1_news_mocks_http(tmp_db, minimal_config, sample_rss_xml) -> None:
    """RSS fetch is mocked — no live network."""
    init_db(tmp_db)

    with _mock_httpx_client(sample_rss_xml):
        result = await ingest_tier_1_news(minimal_config, db_path=str(tmp_db))

    assert result.feeds_attempted == 1
    assert result.feeds_succeeded == 1
    assert result.parsed_entries == 2
    assert result.inserted == 2
    assert result.duplicates == 0
    assert result.failed_feeds == []

    n = query_one("SELECT COUNT(*) AS n FROM news", db_path=tmp_db)["n"]
    assert n == 2

    # Re-run should treat both rows as duplicates.
    with _mock_httpx_client(sample_rss_xml):
        result2 = await ingest_tier_1_news(minimal_config, db_path=str(tmp_db))
    assert result2.inserted == 0
    assert result2.duplicates == 2
