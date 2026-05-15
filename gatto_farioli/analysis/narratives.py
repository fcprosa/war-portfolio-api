"""Rolling narrative memory — deterministic story-cluster tracker.

Goal: turn the firehose of individual headlines into a smaller set of
*storylines* the system can reason about over days/weeks. So when the user
opens the radar tomorrow they see "Hormuz / oil supply / US-China talks
(active, momentum 1.4×, 14 articles over 6d)" rather than re-reading the
same 14 headlines twelve times.

No embeddings. No LLM. Pure rule-based.

Algorithm (deterministic, idempotent):

1. Pull all news in the last `lookback_days` (ordered chronologically).
2. For each article, derive its *content token set* — lowercased non-stopword
   tokens of length ≥ 3 from title + first 240 chars of summary/full_text.
3. For each article, in time order, find the best existing cluster:
       shared_sectors  >= 1                  (sectors gate — required)
       shared_content  >= 2                  (lexical gate — small-corpus friendly)
   Among clusters that pass both gates, pick the one with the highest
   shared_content overlap. Ties broken by smaller cluster_key.
4. Merge into match if found; otherwise create a new cluster whose
   representative token set starts as the article's tokens.
5. As articles join, the cluster's representative token set grows — but
   we keep only the top-K most frequent tokens (K=40) so a noisy cluster
   doesn't bloat into a "matches everything" attractor.
6. After all articles are placed, aggregate per cluster:
       first_seen, last_seen, article_count, avg/max importance,
       momentum_24h, momentum_7d, status, related_tickers.
7. Replace `narrative_clusters` rows in one transaction (DELETE + INSERT).
   cluster_key is sha1(sorted top-10 representative tokens + sorted
   sectors) so identical corpus → identical key set.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from storage.db import DEFAULT_DB_PATH, get_conn

logger = logging.getLogger(__name__)

# ── Tunables (intentionally not config — these are matcher invariants) ─────
# Minimum number of shared *content* tokens (lower-cased, non-stopword) between
# an incoming article and an existing cluster to count as the same story. 2
# is intentionally low: when combined with the sectors gate it filters out
# coincidence without requiring large lexical overlap in short headlines.
MIN_SHARED_CONTENT = 2
MIN_SHARED_SECTORS = 1
# Cap a cluster's representative token set so a long-running cluster doesn't
# turn into an attractor that swallows every article with one or two common
# words.
CLUSTER_TOKEN_CAP = 40
SIGNATURE_KEY_TOKENS = 10
DEFAULT_LOOKBACK_DAYS = 30
MIN_TOKEN_LEN = 3
MAX_TOKEN_LEN = 32
EMERGING_HOURS = 24
ACTIVE_MIN_ARTICLES = 3
ACTIVE_MOMENTUM_FLOOR = 0.5
FADING_LAST_SEEN_DAYS = 7

# Retained for backward compat in __all__; no longer used by the matcher.
JACCARD_THRESHOLD = 0.4
TOP_TERMS_K = 5

# ── Hardcoded English stopwords (zero new deps) ────────────────────────────
# Kept compact and inspectable. Covers the words that dominate news titles
# without being content-bearing: pronouns, common verbs, prepositions,
# news-meta words ('says', 'report', 'breaking', etc.).
_STOPWORDS: frozenset[str] = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "could", "did", "do", "does",
    "doing", "don", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "let", "like", "ll", "may", "me", "might", "mine", "more", "most",
    "must", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "re",
    "said", "same", "say", "says", "she", "should", "so", "some", "such", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "us", "use", "ve", "very", "was", "wasn", "we", "were", "what", "when",
    "where", "which", "while", "who", "whom", "why", "will", "with", "would",
    "you", "your", "yours", "yourself", "yourselves",
    # News meta noise that crowds RSS titles
    "breaking", "report", "reports", "live", "update", "updates", "news", "latest",
    "video", "exclusive", "opinion", "analysis", "watch", "read", "story", "today",
    "yesterday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "year", "years", "day", "days", "week", "weeks", "month", "months",
    "new", "old", "amid", "after", "before", "first", "last", "next", "still",
})

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']{1,30}")


# ── Tokenization ───────────────────────────────────────────────────────────
def tokenize(text: str | None) -> list[str]:
    """Lowercased, stopword-filtered, length-bounded content tokens."""
    if not text:
        return []
    out: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        tok = match.group(0).lower().strip("-'")
        if not (MIN_TOKEN_LEN <= len(tok) <= MAX_TOKEN_LEN):
            continue
        if tok in _STOPWORDS:
            continue
        out.append(tok)
    return out


def article_text(row: dict[str, Any]) -> str:
    """Title + truncated summary/full_text — the basis for cluster matching."""
    title = row.get("title") or ""
    summary = (row.get("summary") or "")[:240]
    full = (row.get("full_text") or "")[:240]
    return f"{title} {summary} {full}"


def article_token_set(row: dict[str, Any]) -> set[str]:
    """Return the article's full content token set used for cluster matching."""
    return set(tokenize(article_text(row)))


def article_top_terms(
    row: dict[str, Any],
    doc_freq: Counter[str] | None = None,
    total_docs: int = 0,
    k: int = SIGNATURE_KEY_TOKENS,
) -> list[str]:
    """Return the k most distinctive content tokens for one article.

    Kept for radar/UI use cases (showing humans what makes a cluster
    distinctive). Ranking is by inverse document frequency when available,
    otherwise just alphabetical to keep it deterministic.
    """
    tokens = tokenize(article_text(row))
    if not tokens:
        return []
    unique = sorted(set(tokens))
    if doc_freq is None:
        return unique[:k]

    def rarity(tok: str) -> tuple[int, str]:
        df = doc_freq.get(tok, 1)
        return (df, tok)

    unique.sort(key=rarity)
    return unique[:k]


def _parse_sectors(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(s).strip() for s in value if str(s).strip()]
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cluster_signature_key(top_terms: list[str], sectors: list[str]) -> str:
    """Stable sha1 key for a cluster signature."""
    payload = "|".join(sorted(t.lower() for t in top_terms)) + "::" + "|".join(sorted(s.lower() for s in sectors))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ── Cluster math ───────────────────────────────────────────────────────────
@dataclass
class _Cluster:
    title: str
    sectors: set[str] = field(default_factory=set)
    # token_counts is the cluster's representative bag-of-words; cluster
    # signature_tokens is derived from it at the end of build.
    token_counts: Counter[str] = field(default_factory=Counter)
    articles: list[dict[str, Any]] = field(default_factory=list)
    related_tickers: set[str] = field(default_factory=set)

    @property
    def signature_tokens(self) -> set[str]:
        """Top-N most frequent tokens in the cluster — used for matching."""
        if not self.token_counts:
            return set()
        return {t for t, _ in self.token_counts.most_common(CLUSTER_TOKEN_CAP)}

    def absorb(self, article_tokens: set[str], article_sectors: set[str]) -> None:
        for tok in article_tokens:
            self.token_counts[tok] += 1
        self.sectors |= article_sectors


def _find_match(
    article_tokens: set[str],
    article_sectors: set[str],
    clusters: list[_Cluster],
) -> _Cluster | None:
    """Pick the best matching existing cluster, or return None.

    Two gates:
      1. shared_sectors >= MIN_SHARED_SECTORS — kills cross-domain merges
         (Hormuz oil headline cannot merge into a CPI/Fed cluster even if
         they share generic tokens like 'higher').
      2. shared_content >= MIN_SHARED_CONTENT — kills coincidental
         sector-tag matches with no lexical overlap.
    """
    if not article_tokens or not article_sectors:
        return None
    best: _Cluster | None = None
    best_overlap = MIN_SHARED_CONTENT - 1
    for c in clusters:
        sec_overlap = len(article_sectors & c.sectors)
        if sec_overlap < MIN_SHARED_SECTORS:
            continue
        content_overlap = len(article_tokens & c.signature_tokens)
        if content_overlap < MIN_SHARED_CONTENT:
            continue
        if content_overlap > best_overlap:
            best, best_overlap = c, content_overlap
    return best


def _detect_tickers(text: str, watchlist_tickers: set[str]) -> set[str]:
    """Pick out whole-token ticker mentions from a news title."""
    if not text or not watchlist_tickers:
        return set()
    out: set[str] = set()
    for tok in re.findall(r"[A-Z][A-Z0-9\-\.]{0,7}", text):
        if tok in watchlist_tickers:
            out.add(tok)
    return out


def _status_for(
    first_seen: datetime,
    last_seen: datetime,
    article_count: int,
    momentum_24h: float,
    now: datetime,
) -> str:
    age_hours = (now - first_seen).total_seconds() / 3600.0
    stale_days = (now - last_seen).total_seconds() / 86400.0
    if stale_days >= FADING_LAST_SEEN_DAYS:
        return "resolved"
    if age_hours < EMERGING_HOURS and article_count >= 2:
        return "emerging"
    if article_count >= ACTIVE_MIN_ARTICLES and momentum_24h >= ACTIVE_MOMENTUM_FLOOR:
        return "active"
    return "fading"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _truncate_title(text: str, n: int = 110) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ── Build pipeline ─────────────────────────────────────────────────────────
@dataclass
class NarrativeBuildResult:
    articles_scanned: int
    clusters_total: int
    clusters_created: int
    clusters_updated: int
    by_status: dict[str, int] = field(default_factory=dict)


def build_narrative_clusters(
    config: dict[str, Any] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    hours_back: int = 24,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    dry_run: bool = False,
) -> NarrativeBuildResult:
    """Rebuild narrative clusters from the news table.

    Fully deterministic and idempotent: same news rows → same cluster_keys.
    Implementation does a DELETE + INSERT each build rather than upsert, so
    cluster boundaries are always consistent with the underlying corpus.
    """
    now = datetime.now(timezone.utc)
    lookback_cutoff_iso = (now - timedelta(days=lookback_days)).isoformat()

    # 1. Pull lookback window of news.
    with get_conn(db_path) as conn:
        news_rows = conn.execute(
            """
            SELECT id, url, source, title, summary, full_text, sectors, importance,
                   published_at, ingested_at
            FROM news
            WHERE COALESCE(published_at, ingested_at) >= ?
            ORDER BY COALESCE(published_at, ingested_at) ASC
            """,
            (lookback_cutoff_iso,),
        ).fetchall()
        news_rows = [dict(r) for r in news_rows]

    if not news_rows:
        return NarrativeBuildResult(0, 0, 0, 0, {})

    # 2. Watchlist ticker set for related_tickers extraction.
    watchlist_tickers: set[str] = set()
    if config:
        for syms in (config.get("watchlist") or {}).values():
            for s in syms or []:
                if s:
                    watchlist_tickers.add(str(s).upper())

    # 3. Cluster construction.
    clusters: list[_Cluster] = []
    for row in news_rows:
        sectors = set(_parse_sectors(row.get("sectors")))
        tokens = article_token_set(row)
        if not tokens or not sectors:
            continue  # need both gates to cluster honestly

        matched = _find_match(tokens, sectors, clusters)
        if matched is None:
            matched = _Cluster(title=_truncate_title(row.get("title") or ""))
            clusters.append(matched)
        matched.absorb(tokens, sectors)
        matched.articles.append(row)
        matched.related_tickers |= _detect_tickers(row.get("title") or "", watchlist_tickers)

    # 4. Aggregate per cluster + status.
    by_status: Counter[str] = Counter()
    rows_to_insert: list[dict[str, Any]] = []
    now_iso = now.isoformat()
    h24 = now - timedelta(hours=24)
    h48 = now - timedelta(hours=48)
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)

    for c in clusters:
        if not c.articles:
            continue
        published_dts = [
            _parse_dt(a.get("published_at") or a.get("ingested_at")) for a in c.articles
        ]
        published_dts = [d for d in published_dts if d is not None]
        if not published_dts:
            continue
        first_seen = min(published_dts)
        last_seen = max(published_dts)

        importances = [float(a.get("importance") or 0.0) for a in c.articles]
        avg_imp = round(sum(importances) / len(importances), 3) if importances else 0.0
        max_imp = round(max(importances), 3) if importances else 0.0

        n_24h = sum(1 for d in published_dts if d >= h24)
        n_24_48 = sum(1 for d in published_dts if h48 <= d < h24)
        n_7d = sum(1 for d in published_dts if d >= d7)
        n_7_14 = sum(1 for d in published_dts if d14 <= d < d7)
        momentum_24h = round(n_24h / max(1, n_24_48), 3)
        momentum_7d = round(n_7d / max(1, n_7_14), 3)

        status = _status_for(first_seen, last_seen, len(c.articles), momentum_24h, now)
        by_status[status] += 1

        # Cluster signature: top-N tokens by within-cluster frequency, sorted
        # so the hash is stable.
        top_terms = [t for t, _ in c.token_counts.most_common(SIGNATURE_KEY_TOKENS)]
        sectors_sorted = sorted(c.sectors)
        cluster_key = cluster_signature_key(top_terms, sectors_sorted)

        signature_payload = json.dumps(
            {"top_terms": sorted(top_terms), "title": c.title},
            ensure_ascii=False,
        )

        rows_to_insert.append({
            "cluster_key": cluster_key,
            "title": c.title,
            "summary": signature_payload,
            "sectors": json.dumps(sectors_sorted, ensure_ascii=False),
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
            "article_count": len(c.articles),
            "avg_importance": avg_imp,
            "max_importance": max_imp,
            "momentum_24h": momentum_24h,
            "momentum_7d": momentum_7d,
            "status": status,
            "related_tickers": json.dumps(sorted(c.related_tickers), ensure_ascii=False),
            "related_markets": json.dumps([], ensure_ascii=False),
            "updated_at": now_iso,
        })

    # 5. Deduplicate by cluster_key (rare, but two separately-created clusters
    # can collapse to the same signature key after enough articles attach).
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows_to_insert:
        key = row["cluster_key"]
        if key in by_key:
            existing = by_key[key]
            existing["article_count"] += row["article_count"]
            existing["first_seen"] = min(existing["first_seen"], row["first_seen"])
            existing["last_seen"] = max(existing["last_seen"], row["last_seen"])
            existing["max_importance"] = max(existing["max_importance"], row["max_importance"])
        else:
            by_key[key] = row
    deduped = list(by_key.values())

    created = 0
    updated = 0
    if not dry_run and deduped:
        with get_conn(db_path) as conn:
            existing_keys = {
                r["cluster_key"]
                for r in conn.execute("SELECT cluster_key FROM narrative_clusters").fetchall()
            }
            conn.execute("DELETE FROM narrative_clusters")
            for row in deduped:
                if row["cluster_key"] in existing_keys:
                    updated += 1
                else:
                    created += 1
                conn.execute(
                    """
                    INSERT INTO narrative_clusters (
                        cluster_key, title, summary, sectors,
                        first_seen, last_seen, article_count,
                        avg_importance, max_importance,
                        momentum_24h, momentum_7d, status,
                        related_tickers, related_markets, updated_at
                    )
                    VALUES (
                        :cluster_key, :title, :summary, :sectors,
                        :first_seen, :last_seen, :article_count,
                        :avg_importance, :max_importance,
                        :momentum_24h, :momentum_7d, :status,
                        :related_tickers, :related_markets, :updated_at
                    )
                    """,
                    row,
                )

    return NarrativeBuildResult(
        articles_scanned=len(news_rows),
        clusters_total=len(deduped),
        clusters_created=created,
        clusters_updated=updated,
        by_status=dict(by_status),
    )


# ── Standalone updater + read helpers ──────────────────────────────────────
def update_narrative_momentum(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Recompute status from current first_seen/last_seen/article_count without
    rebuilding clusters. Cheap. Useful for an in-day refresh.

    Returns the number of rows whose status flipped.
    """
    now = datetime.now(timezone.utc)
    flipped = 0
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, first_seen, last_seen, article_count, momentum_24h, status FROM narrative_clusters"
        ).fetchall()
        for r in rows:
            first = _parse_dt(r["first_seen"])
            last = _parse_dt(r["last_seen"])
            if not first or not last:
                continue
            new_status = _status_for(
                first, last, int(r["article_count"] or 0), float(r["momentum_24h"] or 0.0), now
            )
            if new_status != (r["status"] or ""):
                conn.execute(
                    "UPDATE narrative_clusters SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now.isoformat(), r["id"]),
                )
                flipped += 1
    return flipped


def get_active_narratives(db_path: str | Path = DEFAULT_DB_PATH, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most-actionable active/emerging clusters for the radar.

    Sorted by (momentum_24h × max_importance) desc — what's getting louder AND
    matters.
    """
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT cluster_key, title, summary, sectors, first_seen, last_seen,
                   article_count, avg_importance, max_importance,
                   momentum_24h, momentum_7d, status, related_tickers, related_markets
            FROM narrative_clusters
            WHERE status IN ('emerging', 'active')
            ORDER BY (COALESCE(momentum_24h, 0) * COALESCE(max_importance, 0)) DESC,
                     COALESCE(max_importance, 0) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_context_window(db_path: str | Path = DEFAULT_DB_PATH, days: int = 30) -> dict[str, Any]:
    """Summary view of the rolling narrative shape across the lookback window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n, AVG(article_count) AS avg_articles,
                   AVG(momentum_24h) AS avg_momentum_24h
            FROM narrative_clusters
            WHERE last_seen >= ?
            GROUP BY status
            """,
            (cutoff,),
        ).fetchall()
    return {
        "lookback_days": days,
        "by_status": [dict(r) for r in rows],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "MIN_SHARED_CONTENT",
    "MIN_SHARED_SECTORS",
    "SIGNATURE_KEY_TOKENS",
    "CLUSTER_TOKEN_CAP",
    "NarrativeBuildResult",
    "tokenize",
    "article_token_set",
    "article_top_terms",
    "cluster_signature_key",
    "build_narrative_clusters",
    "update_narrative_momentum",
    "get_active_narratives",
    "get_context_window",
]
