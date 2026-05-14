"""Deterministic news scoring + sector tagging.

Pure rule-based. NO LLM calls. Updates ``news.importance`` (0-10 float)
and ``news.sectors`` (comma-separated tag list) for rows that don't yet
have them.

Importance components:
    +3.0   high-priority keyword in title
    +1.5   medium-priority keyword in title
    +1.5   high-priority keyword in summary/full_text
    +0.75  medium-priority keyword in summary/full_text
    +1.0   source hostname is in news_sources.tier_1
    +0.5   source hostname is in news_sources.tier_2
    +1.5   published within last 6h
    +0.5   published within last 24h
    -1.0   published more than 72h ago
    capped to [0, 10]

Sectors are independent and additive (one article can be multi-tagged):
    oil, fertilizer, rates_fed, defense, prediction_market, shipping,
    gold, broad_market, geopolitics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from storage.db import DEFAULT_DB_PATH, get_conn

# Sector keyword map. Lowercased; matched as plain substrings.
SECTOR_PATTERNS: dict[str, tuple[str, ...]] = {
    "oil": (
        "oil price", "crude", "brent", "wti", "opec", "gasoline", "refinery",
        "tanker", "vlcc", "spr", "saudi aramco", "exxon", "chevron",
    ),
    "fertilizer": (
        "fertilizer", "fertiliser", "urea", "potash", "ammonia", "nitrogen",
        "phosphate", "cf industries", "mosaic", "nutrien",
    ),
    "rates_fed": (
        "federal reserve", "fed ", "powell", "cpi", "pce", "fomc",
        "rate cut", "rate hike", "rate decision", "inflation", "interest rate",
        "jobs report", "payrolls", "unemployment",
    ),
    "defense": (
        "nato", "rearmament", "defense spending", "defence spending", "military",
        "rheinmetall", "lockheed", "raytheon", "northrop", "bae systems",
        "arms deal", "weapons package", "missile",
    ),
    "prediction_market": (
        "kalshi", "polymarket", "prediction market",
    ),
    "shipping": (
        "shipping", "container", "freight rate", "port congestion", "drewry",
        "baltic dry", "houthi",
    ),
    "gold": (
        "gold price", "gold rally", "gold high", "silver price", "precious metal",
        "bullion",
    ),
    "broad_market": (
        "s&p 500", "sp500", "nasdaq", "dow jones", "treasury yield",
        "bond yield", "10-year", "10y", "vix",
    ),
    "geopolitics": (
        "hormuz", "iran", "israel", "gaza", "ukraine", "russia", "china",
        "ceasefire", "sanction", "mideast", "middle east", "houthi", "taiwan",
    ),
}


@dataclass
class NewsScoringResult:
    """Structured summary of one scoring run."""

    rows_scanned: int
    rows_scored: int
    rows_skipped: int
    avg_score: float | None
    max_score: float | None
    top_sectors: dict[str, int] = field(default_factory=dict)


def _hostname(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_kw(kw: str) -> str:
    return (kw or "").strip().lower()


def _hosts_for_tier(config: dict[str, Any], tier_key: str) -> set[str]:
    urls = (config.get("news_sources", {}) or {}).get(tier_key, []) or []
    return {_hostname(u) for u in urls if u}


def _parse_published(published_at: str | None) -> datetime | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _recency_bonus(published: datetime | None, now: datetime) -> float:
    if published is None:
        return 0.0
    age_hours = (now - published).total_seconds() / 3600.0
    if age_hours < 0:  # clock skew — treat as fresh, not future
        return 1.5
    if age_hours < 6:
        return 1.5
    if age_hours < 24:
        return 0.5
    if age_hours > 72:
        return -1.0
    return 0.0


def _matches_any(text: str, keywords: Iterable[str]) -> bool:
    if not text:
        return False
    return any(kw in text for kw in keywords if kw)


def _tag_sectors(title_lower: str, body_lower: str) -> list[str]:
    """Return all sector tags whose patterns appear in title or body."""
    combined = f"{title_lower} {body_lower}"
    tags: list[str] = []
    for sector, patterns in SECTOR_PATTERNS.items():
        if any(p in combined for p in patterns):
            tags.append(sector)
    return tags


def score_news(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> NewsScoringResult:
    """Score every news row missing importance/sectors (or all if force=True)."""

    high_kw = [_normalize_kw(k) for k in (config.get("keywords_high_priority") or []) if k]
    med_kw = [_normalize_kw(k) for k in (config.get("keywords_medium_priority") or []) if k]

    tier_1_hosts = _hosts_for_tier(config, "tier_1")
    tier_2_hosts = _hosts_for_tier(config, "tier_2")

    where = "" if force else "WHERE importance IS NULL OR sectors IS NULL"
    select_sql = f"SELECT id, url, title, summary, full_text, published_at, importance, sectors FROM news {where}"

    now = datetime.now(timezone.utc)
    scored_rows: list[tuple[float, str, int]] = []
    sector_counts: dict[str, int] = {}
    scores: list[float] = []
    rows_scanned = 0
    rows_skipped = 0

    with get_conn(db_path) as conn:
        for row in conn.execute(select_sql).fetchall():
            rows_scanned += 1
            if not force and row["importance"] is not None and row["sectors"] is not None:
                rows_skipped += 1
                continue

            title_lc = (row["title"] or "").lower()
            body_lc = ((row["summary"] or "") + " " + (row["full_text"] or "")).lower()

            score = 0.0
            if _matches_any(title_lc, high_kw):
                score += 3.0
            if _matches_any(title_lc, med_kw):
                score += 1.5
            if _matches_any(body_lc, high_kw):
                score += 1.5
            if _matches_any(body_lc, med_kw):
                score += 0.75

            host = _hostname(row["url"])
            if host in tier_1_hosts:
                score += 1.0
            elif host in tier_2_hosts:
                score += 0.5

            published = _parse_published(row["published_at"])
            score += _recency_bonus(published, now)

            score = max(0.0, min(10.0, round(score, 2)))
            tags = _tag_sectors(title_lc, body_lc)
            sectors_str = ",".join(tags) if tags else None

            scored_rows.append((score, sectors_str or "", int(row["id"])))
            scores.append(score)
            for t in tags:
                sector_counts[t] = sector_counts.get(t, 0) + 1

        if not dry_run and scored_rows:
            conn.executemany(
                "UPDATE news SET importance = ?, sectors = ? WHERE id = ?",
                [(s, sec or None, rid) for s, sec, rid in scored_rows],
            )

    return NewsScoringResult(
        rows_scanned=rows_scanned,
        rows_scored=len(scored_rows),
        rows_skipped=rows_skipped,
        avg_score=round(sum(scores) / len(scores), 2) if scores else None,
        max_score=max(scores) if scores else None,
        top_sectors=dict(sorted(sector_counts.items(), key=lambda kv: kv[1], reverse=True)),
    )


__all__ = ["NewsScoringResult", "SECTOR_PATTERNS", "score_news"]
