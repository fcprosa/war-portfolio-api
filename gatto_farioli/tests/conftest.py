"""Shared pytest fixtures — no network access."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Package root must be on sys.path before any gatto_farioli imports.
_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from _bootstrap import ensure_import_paths

ensure_import_paths()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Fresh SQLite file for isolated tests."""
    return tmp_path / "test_argos.db"


@pytest.fixture
def minimal_config() -> dict:
    """Minimal valid config dict — no filesystem or network required."""
    return {
        "portfolio": {"positions": [], "prediction_markets": []},
        "theses": {},
        "watchlist": {"oil": ["XOM"]},
        "news_sources": {
            "tier_1": ["https://feeds.example.com/news.rss"],
        },
        "alerts": {},
        "llm": {},
        "schedule": {},
    }


@pytest.fixture
def sample_rss_xml() -> bytes:
    """Tiny RSS 2.0 document with two entries (one duplicate URL variant)."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Wire</title>
    <item>
      <title>Oil rises on Hormuz tension</title>
      <link>https://example.com/story/1?utm_source=twitter</link>
      <description><p>Crude benchmarks moved higher.</p></description>
      <pubDate>Wed, 14 May 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Fed holds rates steady</title>
      <link>https://example.com/story/2</link>
      <description>Central bank unchanged.</description>
      <pubDate>Wed, 14 May 2026 13:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""
