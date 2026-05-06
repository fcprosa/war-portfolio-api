"""RSS news ingestion for Gatto Farioli Session 1.

This module ingests tier_1 RSS feeds only. It does not ask an LLM to classify,
summarize, or score articles yet; Session 3 will fill those enrichment fields.
For now, sectors/sentiment/importance stay NULL by design.
"""

from __future__ import annotations

import hashlib
import html
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from storage.db import DEFAULT_DB_PATH, get_conn

USER_AGENT = "GattoFarioli/0.1 local macro intelligence RSS reader"


@dataclass(frozen=True)
class NewsIngestionResult:
    """Summary of one news ingestion run."""

    feeds_attempted: int
    feeds_succeeded: int
    parsed_entries: int
    inserted: int
    duplicates: int
    failed_feeds: list[str]


def normalize_url(url: str) -> str:
    """Normalize a URL before hashing so tracking params do not defeat dedupe."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def url_hash(url: str) -> str:
    """Return a stable sha256 hash for a normalized URL."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def clean_text(value: str | None) -> str | None:
    """Convert feed HTML/text into a compact plain-text string."""
    if not value:
        return None
    soup = BeautifulSoup(value, "html.parser")
    return " ".join(html.unescape(soup.get_text(" ")).split()) or None


def parse_published_at(entry: Any) -> str | None:
    """Parse the best available RSS published timestamp into UTC ISO format."""
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if not raw:
            continue
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
    return None


def source_name(feed_url: str, parsed_feed: Any) -> str:
    """Choose a readable source name from feed metadata or the URL hostname."""
    title = parsed_feed.feed.get("title") if getattr(parsed_feed, "feed", None) else None
    if title:
        return clean_text(title) or title
    return urlsplit(feed_url).netloc.replace("www.", "")


async def fetch_feed(client: httpx.AsyncClient, feed_url: str) -> tuple[str, bytes | None, str | None]:
    """Fetch one RSS feed and return either content bytes or an error string."""
    try:
        response = await client.get(feed_url, timeout=20.0)
        response.raise_for_status()
        return feed_url, response.content, None
    except Exception as exc:  # Source failures are expected; caller logs and continues.
        return feed_url, None, str(exc)


def entry_to_row(feed_url: str, parsed_feed: Any, entry: Any) -> dict[str, Any] | None:
    """Convert one feedparser entry into a database-ready row."""
    link = entry.get("link") or entry.get("id") or ""
    normalized = normalize_url(link)
    title = clean_text(entry.get("title"))
    if not normalized or not title:
        return None

    summary = clean_text(entry.get("summary") or entry.get("description"))
    return {
        "url_hash": url_hash(normalized),
        "url": normalized,
        "source": source_name(feed_url, parsed_feed),
        "title": title,
        "summary": None,
        "full_text": summary,
        "sectors": None,
        "sentiment": None,
        "importance": None,
        "published_at": parse_published_at(entry),
    }


def upsert_news(rows: list[dict[str, Any]], db_path: str = str(DEFAULT_DB_PATH), dry_run: bool = False) -> tuple[int, int]:
    """Insert news rows, ignoring duplicates by url_hash, and return inserted/duplicate counts."""
    if dry_run:
        return len(rows), 0
    inserted = 0
    duplicates = 0
    with get_conn(db_path) as conn:
        for row in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO news (
                    url_hash, url, source, title, summary, full_text, sectors,
                    sentiment, importance, published_at
                ) VALUES (
                    :url_hash, :url, :source, :title, :summary, :full_text,
                    :sectors, :sentiment, :importance, :published_at
                )
                """,
                row,
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
    return inserted, duplicates


async def ingest_tier_1_news(config: dict[str, Any], db_path: str = str(DEFAULT_DB_PATH), dry_run: bool = False) -> NewsIngestionResult:
    """Fetch configured tier_1 RSS feeds asynchronously and store new articles."""
    feed_urls = config.get("news_sources", {}).get("tier_1", [])
    rows: list[dict[str, Any]] = []
    failed: list[str] = []
    succeeded = 0

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}, follow_redirects=True) as client:
        tasks = [fetch_feed(client, feed_url) for feed_url in feed_urls]
        for feed_url, content, error in await asyncio.gather(*tasks):
            if error or content is None:
                failed.append(f"{feed_url} — {error}")
                continue
            parsed = feedparser.parse(content)
            if parsed.bozo and not parsed.entries:
                failed.append(f"{feed_url} — parse error: {parsed.bozo_exception}")
                continue
            succeeded += 1
            for entry in parsed.entries:
                row = entry_to_row(feed_url, parsed, entry)
                if row:
                    rows.append(row)

    inserted, duplicates = upsert_news(rows, db_path=db_path, dry_run=dry_run)
    return NewsIngestionResult(
        feeds_attempted=len(feed_urls),
        feeds_succeeded=succeeded,
        parsed_entries=len(rows),
        inserted=inserted,
        duplicates=duplicates,
        failed_feeds=failed,
    )
