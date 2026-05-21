#!/usr/bin/env python
"""Verification harness for the Gatto Farioli local engine.

Runs a series of self-checks against a *temporary* SQLite database so the
real ``argos.db`` is never touched. Designed to be safe to run in CI or
as a smoke test after every change.

Usage:
    cd gatto_farioli
    .venv/bin/python scripts/verify.py

Exit code 0 on success, non-zero on first failure (with details printed).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from pathlib import Path

# Make the gatto_farioli package importable when running directly.
PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


FAILED: list[str] = []
PASSED: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    marker = "PASS" if ok else "FAIL"
    line = f"[{marker}] {name}" + (f" — {detail}" if detail else "")
    print(line)
    (PASSED if ok else FAILED).append(name)


def _check(name: str, fn) -> None:
    try:
        fn()
        _record(name, True)
    except AssertionError as exc:
        _record(name, False, str(exc) or "assertion failed")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc().splitlines()[-1]
        _record(name, False, f"{exc} ({tb})")


# ── 1. Config loads ────────────────────────────────────────────────────────
def check_config_loads() -> None:
    from config import load_config

    cfg = load_config(PROJECT_DIR / "config.yaml")
    assert isinstance(cfg, dict), "config must be a dict"
    for section in ("portfolio", "theses", "watchlist", "news_sources", "alerts", "llm", "schedule"):
        assert section in cfg, f"missing required section: {section}"
    positions = cfg["portfolio"].get("positions") or []
    assert positions, "portfolio.positions must not be empty"


# ── 2. DB initializes ──────────────────────────────────────────────────────
def check_db_initializes(tmp_db: Path) -> None:
    from storage.db import init_db, query_one

    init_db(tmp_db)
    expected_tables = (
        "news", "prices", "macro", "prediction_markets",
        "portwatch", "positions", "theses", "alerts", "briefs", "runs",
        "narrative_clusters", "opportunity_candidates", "opportunity_outcomes",
        "market_universe", "source_health",
    )
    for table in expected_tables:
        row = query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
            db_path=tmp_db,
        )
        assert row is not None, f"table {table!r} missing after init_db"


# ── 3. URL normalization dedupes tracking params ───────────────────────────
def check_url_normalization() -> None:
    from ingestion.news import normalize_url, url_hash

    base = "https://example.com/article?id=42"
    polluted = [
        f"{base}&utm_source=newsletter",
        f"{base}&utm_campaign=foo&fbclid=xyz",
        f"{base}&gclid=abc&UTM_MEDIUM=email",
    ]
    expected = normalize_url(base)
    for variant in polluted:
        assert normalize_url(variant) == expected, f"normalize_url didn't strip tracking params from {variant}"
        assert url_hash(variant) == url_hash(base), f"url_hash differs across variants of the same article"

    # Case insensitivity on scheme/host
    assert normalize_url("HTTPS://Example.COM/x") == normalize_url("https://example.com/x")


# ── 4. Brief on empty DB does not crash ────────────────────────────────────
def check_brief_on_empty_db(tmp_db: Path) -> None:
    from analysis.brief import generate_daily_brief
    from config import load_config
    from storage.db import init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    text = generate_daily_brief(cfg, db_path=tmp_db, dry_run=True)
    assert isinstance(text, str), "brief must return a string"
    assert "GATTO FARIOLI — DAILY EDGE BRIEF" in text, "brief missing header"
    assert "Executive Verdict" in text, "brief missing executive verdict section"
    assert "Portfolio Impact" in text, "brief missing portfolio section"
    assert "Prediction Markets" in text, "brief missing prediction markets section"
    assert "Claude Context Block" in text, "brief missing claude context block"


# ── 5. --health prints expected sections ───────────────────────────────────
def check_health_runs(tmp_db: Path) -> None:
    from run import print_health
    from storage.db import init_db

    init_db(tmp_db)
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_health(tmp_db)
    output = buf.getvalue()
    assert "Gatto Farioli health" in output, "--health missing header"
    for table in ("news", "prices", "positions", "prediction_markets", "narrative_clusters", "market_universe"):
        assert f"{table}:" in output, f"--health output missing '{table}:' line"


# ── 6. Bonus: thesis resolver handles ticker patterns ──────────────────────
def check_thesis_signal_resolver(tmp_db: Path) -> None:
    from analysis.thesis import resolve_signal
    from storage.db import init_db, get_conn

    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
            ("IPI", "2026-05-14", 43.5),
        )
        observed = resolve_signal(conn, "ipi_above_35")
        not_observed = resolve_signal(conn, "ipi_above_50")
        uncertain = resolve_signal(conn, "russia_sanctions_intact")

    assert observed.state == "observed", f"expected observed, got {observed.state}"
    assert not_observed.state == "not_observed", f"expected not_observed, got {not_observed.state}"
    assert uncertain.state == "uncertain", f"expected uncertain, got {uncertain.state}"


# ── 7. init_db is idempotent for Phase A–C tables ─────────────────────────
def check_init_db_idempotent(tmp_db: Path) -> None:
    from storage.db import init_db, query_one

    init_db(tmp_db)
    init_db(tmp_db)
    for table in ("narrative_clusters", "market_universe", "source_health"):
        row = query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
            db_path=tmp_db,
        )
        assert row is not None, f"{table} missing after double init_db"


# ── 8. Narrative clustering merges repeated headlines ────────────────────
def check_narrative_clustering(tmp_db: Path) -> None:
    from analysis.narratives import build_narrative_clusters
    from analysis.news_score import score_news
    from config import load_config
    from storage.db import get_conn, init_db, query_one

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")

    # Insert 4 near-duplicate Hormuz/oil headlines plus 2 unrelated ones to
    # confirm clustering keeps unrelated stories in their own buckets.
    rows = [
        ("u1", "https://x.example/1", "Reuters",
         "Oil prices jump on Hormuz tensions as US pressures Iran",
         "Crude markets reacted sharply to renewed Hormuz strait risk.",
         "2026-05-14T12:00:00+00:00"),
        ("u2", "https://x.example/2", "Bloomberg",
         "Brent crude rises again on Hormuz disruption fears",
         "Brent climbed as Hormuz transit fears intensified.",
         "2026-05-14T14:00:00+00:00"),
        ("u3", "https://x.example/3", "FT",
         "Iran-Hormuz crisis pushes oil higher for third day",
         "Oil supply concerns mount over Hormuz blockade scenario.",
         "2026-05-14T16:00:00+00:00"),
        ("u4", "https://x.example/4", "WSJ",
         "Hormuz oil chokepoint risk keeps crude prices elevated",
         "Tanker insurers warn of Hormuz transit risk premium.",
         "2026-05-14T18:00:00+00:00"),
        # Unrelated to oil/Hormuz — should land in its own cluster.
        ("u5", "https://x.example/5", "CNBC",
         "Fed officials signal patience on rate cuts as CPI ticks higher",
         "FOMC commentary suggests Fed will hold rates as inflation persists.",
         "2026-05-14T13:00:00+00:00"),
        ("u6", "https://x.example/6", "CNBC",
         "CPI surprise pushes Fed rate-cut bets out further",
         "Treasury yields rose after the CPI print exceeded expectations.",
         "2026-05-14T15:00:00+00:00"),
    ]
    with get_conn(tmp_db) as conn:
        for url_hash, url, source, title, summary, published in rows:
            conn.execute(
                "INSERT INTO news (url_hash, url, source, title, summary, published_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (url_hash, url, source, title, summary, published),
            )

    # Score so sectors get populated (clustering needs at least one sector).
    score_news(cfg, db_path=tmp_db)
    result = build_narrative_clusters(cfg, db_path=tmp_db)

    assert result.articles_scanned == 6, f"expected 6, got {result.articles_scanned}"
    # Hormuz/oil group should merge to a single cluster; CPI/Fed group should
    # form its own. So the test expects ≤ 4 clusters total (often 2).
    assert result.clusters_total <= 4, f"too many clusters: {result.clusters_total}"

    # Confirm the largest cluster swallowed at least 3 of the 4 Hormuz headlines.
    largest = query_one(
        "SELECT article_count FROM narrative_clusters ORDER BY article_count DESC LIMIT 1",
        db_path=tmp_db,
    )
    assert largest is not None and largest["article_count"] >= 3, (
        f"largest cluster has {largest['article_count'] if largest else 'no'} articles; expected ≥ 3"
    )


# ── 9. Narrative status values are represented ────────────────────────────
def check_narrative_status_values(tmp_db: Path) -> None:
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    allowed = {"emerging", "active", "fading", "resolved"}
    with get_conn(tmp_db) as conn:
        for status in allowed:
            conn.execute(
                """
                INSERT INTO narrative_clusters (
                    cluster_key, title, summary, sectors,
                    first_seen, last_seen, article_count,
                    avg_importance, max_importance,
                    momentum_24h, momentum_7d, status,
                    related_tickers, related_markets, updated_at
                ) VALUES (?, ?, '{}', '[]', ?, ?, 1, 1.0, 1.0, 1.0, 1.0, ?, '[]', '[]', ?)
                """,
                (f"key_{status}", f"title {status}", "2026-05-01T00:00:00+00:00",
                 "2026-05-14T00:00:00+00:00", status, "2026-05-14T00:00:00+00:00"),
            )
        rows = conn.execute("SELECT DISTINCT status FROM narrative_clusters").fetchall()
    found = {r["status"] for r in rows}
    assert found == allowed, f"unexpected narrative statuses: {found}"


# ── 10. Kalshi categorizer maps known buckets correctly ───────────────────
def check_kalshi_categorizer() -> None:
    from ingestion.kalshi import categorize_kalshi_event

    cases = [
        ({"category": "Sports", "title": "MLB game outcome"}, "sports"),
        ({"category": "Climate and Weather", "title": "Hottest day of the year"}, "weather"),
        ({"category": "Politics", "title": "2026 senate race"}, "politics"),
        ({"category": "Economics", "title": "Will CPI exceed 3% in May 2026"}, "inflation"),
        ({"category": "Economics", "title": "Will Fed cut rates in June"}, "rates"),
        ({"category": "Financials", "title": "S&P 500 close"}, "macro"),
        ({"category": "World", "title": "Iran/Hormuz incident"}, "geopolitics"),
        ({"category": None, "title": "Bitcoin price by year end"}, "crypto"),
        ({"category": "Health", "title": "Random health story"}, "other"),
    ]
    for event, expected in cases:
        got = categorize_kalshi_event(event)
        assert got == expected, f"categorize({event}) = {got!r}, expected {expected!r}"


# ── 11. Sports markets are excluded under default config ──────────────────
def check_kalshi_sports_excluded_by_default() -> None:
    from ingestion.kalshi import filter_kalshi_events
    from config import load_config

    cfg = load_config(PROJECT_DIR / "config.yaml")
    kalshi_cfg = cfg.get("kalshi") or {}
    inc = kalshi_cfg.get("include_categories") or []
    exc = kalshi_cfg.get("exclude_categories") or []
    assert "sports" in exc, f"sports must be excluded by default; got exclude={exc}"

    events = [
        {"category": "Sports", "title": "MLB game outcome"},
        {"category": "Politics", "title": "Senate race"},
        {"category": "Climate and Weather", "title": "Hurricane season count"},
    ]
    filtered = filter_kalshi_events(events, include_categories=inc, exclude_categories=exc)
    cats = [c for _, c in filtered]
    assert "sports" not in cats, f"sports leaked through default filter: {cats}"
    assert "politics" in cats and "weather" in cats, f"non-sports dropped: {cats}"


# ── 12. source_health upserts and surfaces unhealthy sources ──────────────
def check_source_health(tmp_db: Path) -> None:
    from storage.db import init_db
    from storage.source_health import (
        list_unhealthy,
        record_failure,
        record_success,
    )

    init_db(tmp_db)

    record_success("http://feed.example/ok", "first ok", db_path=tmp_db)
    record_failure("http://feed.example/broken", "timeout", db_path=tmp_db)
    record_failure("http://feed.example/broken", "timeout again", db_path=tmp_db)
    record_success("http://feed.example/recovered", "200", db_path=tmp_db)
    record_failure("http://feed.example/recovered", "500 later", db_path=tmp_db)

    unhealthy = list_unhealthy(db_path=tmp_db)
    sources = {r["source"] for r in unhealthy}
    assert "http://feed.example/broken" in sources, "consistently-failing source missing"
    assert "http://feed.example/recovered" in sources, "source with recent failure missing"
    assert "http://feed.example/ok" not in sources, "healthy source surfaced as unhealthy"

    broken = next(r for r in unhealthy if r["source"] == "http://feed.example/broken")
    assert broken["failure_count"] >= 2, f"failure_count not bumped: {broken}"


# ── 13. Opportunity candidates upsert without duplicating ─────────────────
def check_opportunity_upsert_idempotent(tmp_db: Path) -> None:
    from analysis.opportunities import ACTION_WATCH, upsert_opportunity_candidates, _Candidate
    from storage.db import init_db, query_one

    init_db(tmp_db)
    c = _Candidate(
        candidate_key="equity:VERIFY",
        title="Verify",
        summary="",
        source_type="equity",
        related_ticker="VERIFY",
        related_market_ticker=None,
        related_narrative_id=None,
        score=40.0,
        confidence=4.0,
        action=ACTION_WATCH,
        signals_count=1,
        missing_data=[],
        evidence={},
    )
    upsert_opportunity_candidates([c], tmp_db)
    c.score = 55.0
    upsert_opportunity_candidates([c], tmp_db)
    n = query_one("SELECT COUNT(*) AS n FROM opportunity_candidates", db_path=tmp_db)["n"]
    assert n == 1, f"expected 1 row, got {n}"


def check_opportunity_action_guards(tmp_db: Path) -> None:
    from analysis.opportunities import ACTION_POSSIBLE_TRADE, score_opportunities
    from config import load_config
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO news (url_hash, url, source, title, summary, sectors, importance, published_at) "
            "VALUES ('only', 'https://x/o', 'X', 'One headline oil move', 'text', 'oil', 8.0, ?)",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                cluster_key, title, summary, sectors, first_seen, last_seen,
                article_count, avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (
                'nar_only', 'Broad oil narrative only', '{}', '["oil"]',
                ?, ?, 10, 5.0, 6.0, 2.0, 1.0, 'active', '[]', '[]', ?
            )
            """,
            (now, now, now),
        )
        conn.execute(
            """
            INSERT INTO market_universe (
                platform, symbol, title, category, status, volume_24h,
                yes_price, no_price, discovered_at, updated_at, metadata
            ) VALUES (
                'kalshi', 'KX-POL-HIGH', 'Presidential market', 'politics', 'open', 99999,
                0.4, 0.6, datetime('now'), datetime('now'), '{}'
            )
            """
        )
    score_opportunities(cfg, db_path=tmp_db)
    from storage.db import query_one as q1

    for key in ("equity:CVX", "kalshi:KX-POL-HIGH"):
        row = q1(
            "SELECT action FROM opportunity_candidates WHERE candidate_key = ?",
            (key,),
            db_path=tmp_db,
        )
        if row:
            assert row["action"] != ACTION_POSSIBLE_TRADE, f"{key} must not be POSSIBLE_TRADE"


# ── 14. News scoring writes back importance + sectors ──────────────────────
def check_news_scoring(tmp_db: Path) -> None:
    from analysis.news_score import score_news
    from config import load_config
    from storage.db import init_db, get_conn, query_one

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO news (url_hash, url, source, title, summary, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "hash_oil_iran",
                "https://example.com/x",
                "Example",
                "Iran threatens to close Hormuz strait as Brent crude jumps",
                "Brent crude price reacts to Iran tensions over Hormuz.",
                "2026-05-14T12:00:00+00:00",
            ),
        )

    result = score_news(cfg, db_path=tmp_db)
    assert result.rows_scored == 1, f"expected 1 scored, got {result.rows_scored}"
    row = query_one("SELECT importance, sectors FROM news LIMIT 1", db_path=tmp_db)
    assert row["importance"] is not None and row["importance"] > 0, "importance not written"
    sectors = row["sectors"] or ""
    assert "geopolitics" in sectors or "oil" in sectors, f"expected geopolitics/oil tag, got {sectors!r}"


# ── 16. Radar on empty DB ─────────────────────────────────────────────────
def check_radar_on_empty_db(tmp_db: Path) -> None:
    from analysis.radar import generate_daily_radar
    from config import load_config
    from storage.db import init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    text = generate_daily_radar(cfg, db_path=tmp_db, dry_run=True)
    assert isinstance(text, str), "radar must return a string"
    assert "Top opportunities" in text, "radar missing Top opportunities section"
    assert "Active & emerging narratives" in text, "radar missing narratives section"
    assert "Source-health warnings" in text, "radar missing Source-health warnings section"


# ── 17. Radar action ordering ─────────────────────────────────────────────
def check_radar_separates_possible_trade_from_watch(tmp_db: Path) -> None:
    from analysis.opportunities import ACTION_POSSIBLE_TRADE, ACTION_WATCH
    from analysis.radar import generate_daily_radar
    from config import load_config
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.executemany(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status
            ) VALUES (?, ?, '', 'equity', ?, ?, ?, 1, '[]', '{}', ?, ?, 'open')
            """,
            [
                ("equity:W", "Verify Watch Title", 50.0, 5.0, ACTION_WATCH, now, now),
                ("equity:P", "Verify Possible Trade Title", 90.0, 9.0, ACTION_POSSIBLE_TRADE, now, now),
            ],
        )
    text = generate_daily_radar(cfg, db_path=tmp_db, dry_run=True)
    assert text.index("Verify Possible Trade Title") < text.index("Verify Watch Title"), (
        "POSSIBLE_TRADE must appear before WATCH in radar output"
    )


# ── 18. Polymarket categorizer maps known buckets ─────────────────────────
def check_polymarket_categorizer() -> None:
    from ingestion.polymarket import categorize_polymarket_event

    cases = [
        ({"category": "Sports", "question": "MLB game outcome"}, "sports"),
        ({"category": "Weather", "question": "Hottest day of the year"}, "weather"),
        ({"category": "Politics", "question": "2026 senate race"}, "politics"),
        ({"question": "Will CPI exceed 3% in May 2026"}, "inflation"),
        ({"question": "Will Fed cut rates in June"}, "rates"),
        ({"category": "Finance", "question": "S&P 500 close"}, "macro"),
        ({"question": "Iran/Hormuz incident"}, "geopolitics"),
        ({"question": "Bitcoin price by year end"}, "crypto"),
        ({"category": "Health", "question": "Random health story"}, "other"),
    ]
    for event, expected in cases:
        got = categorize_polymarket_event(event)
        assert got == expected, f"categorize({event}) = {got!r}, expected {expected!r}"


# ── 19. Polymarket sports excluded by default config ───────────────────────
def check_polymarket_sports_excluded_by_default() -> None:
    from ingestion.polymarket import filter_polymarket_events
    from config import load_config

    cfg = load_config(PROJECT_DIR / "config.yaml")
    poly_cfg = cfg.get("polymarket") or {}
    inc = poly_cfg.get("include_categories") or []
    exc = poly_cfg.get("exclude_categories") or []
    assert "sports" in exc, f"sports must be excluded by default; got exclude={exc}"

    events = [
        {"category": "Sports", "question": "MLB game outcome"},
        {"category": "Politics", "question": "Senate race"},
        {"category": "Weather", "question": "Hurricane season count"},
    ]
    filtered = filter_polymarket_events(events, include_categories=inc, exclude_categories=exc)
    cats = [c for _, c in filtered]
    assert "sports" not in cats, f"sports leaked through default filter: {cats}"
    assert "politics" in cats and "weather" in cats, f"non-sports dropped: {cats}"


# ── 20. Quality bar downgrades POSSIBLE_TRADE when invalidation missing ─────
def check_quality_bar_downgrades_possible_trade(tmp_db: Path) -> None:
    from analysis.opportunities import score_opportunities
    from config import load_config
    from storage.db import get_conn, init_db, query_one

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (
                'nar_qb', 'Iran oil escalation narrative', '{}', '["oil"]',
                ?, ?, 8, 6.0, 7.0, 2.0, 1.0, 'active', '["CVX"]', '[]', ?
            )
            """,
            (now, now, now),
        )
        conn.execute(
            """
            INSERT INTO news (url_hash, url, source, title, summary, sectors, importance, published_at)
            VALUES ('qb1', 'https://x/qb1', 'Reuters', 'Oil jumps on Hormuz', 'Crude up', 'oil', 8.0, ?)
            """,
            (now,),
        )
    cfg["watchlist"] = {"oil": ["CVX"]}
    score_opportunities(cfg, db_path=tmp_db)
    row = query_one(
        "SELECT action, quality_bar_passed, quality_bar_missing FROM opportunity_candidates WHERE related_ticker = 'CVX'",
        db_path=tmp_db,
    )
    assert row is not None, "expected equity:CVX candidate"
    assert row["action"] == "WATCH", f"expected WATCH, got {row['action']}"
    assert row["quality_bar_passed"] == 0
    missing = json.loads(row["quality_bar_missing"] or "[]")
    assert "invalidation_trigger" in missing


# ── 21. Radar surfaces quality bar fields and exceptions ──────────────────
def check_radar_quality_bar_surface(tmp_db: Path) -> None:
    from analysis.radar import generate_daily_radar
    from config import load_config
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status,
                catalyst_path, invalidation_trigger, risk_reward_summary,
                quality_bar_passed, quality_bar_missing
            ) VALUES (
                'equity:PASS', 'QB Pass Row', '', 'equity', 80.0, 8.0, 'WATCH', 3,
                '[]', '{}', ?, ?, 'open',
                'Active catalyst path', 'close outside [10, 20]', '+5% / -3% (30d band)', 1, '[]'
            )
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status,
                catalyst_path, invalidation_trigger, risk_reward_summary,
                quality_bar_passed, quality_bar_missing
            ) VALUES (
                'equity:FAIL', 'QB Fail Row', '', 'equity', 70.0, 7.0, 'WATCH', 3,
                '[]', '{}', ?, ?, 'open',
                NULL, NULL, NULL, 0, '["invalidation_trigger"]'
            )
            """,
            (now, now),
        )
    text = generate_daily_radar(cfg, db_path=tmp_db, dry_run=True)
    assert "catalyst:" in text
    assert "invalidate if:" in text
    assert "R/R:" in text
    assert "## Quality bar exceptions" in text
    assert "QB Fail Row" in text


# ── 22. Outcomes snapshot creates row for POSSIBLE_TRADE ───────────────────
def check_outcomes_snapshot_creates_row(tmp_db: Path) -> None:
    from analysis.outcomes import snapshot_open_opportunities
    from config import load_config
    from storage.db import get_conn, init_db, query_one

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, related_ticker,
                score, confidence, action, signals_count, missing_data, evidence,
                created_at, last_seen, status
            ) VALUES (
                'equity:VFY', 'VFY trade', '', 'equity', 'VFY',
                85.0, 8.5, 'POSSIBLE_TRADE', 3, '[]', '{}', ?, ?, 'open'
            )
            """,
            (now, now),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('VFY', '2026-05-14', 42.0)"
        )
    snapshot_open_opportunities(cfg, tmp_db)
    rows = query_one("SELECT COUNT(*) AS n FROM opportunity_outcomes", db_path=tmp_db)
    assert rows["n"] == 1, f"expected 1 outcome row, got {rows['n']}"
    row = query_one(
        "SELECT instrument_kind, entry_price FROM opportunity_outcomes WHERE candidate_key = 'equity:VFY'",
        db_path=tmp_db,
    )
    assert row["instrument_kind"] == "equity"
    assert row["entry_price"] is not None


# ── 23. Outcomes resolve classifies hit vs miss ────────────────────────────
def check_outcomes_resolve_hit_miss(tmp_db: Path) -> None:
    from analysis.outcomes import resolve_open_outcomes
    from config import load_config
    from storage.db import get_conn, init_db, query_all

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    snap = "2026-05-01T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:VFY_HIT', ?, 'POSSIBLE_TRADE',
                'equity', 'VFY', 100.0, 7, 'open'
            )
            """,
            (snap,),
        )
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:VFY_MISS', ?, 'POSSIBLE_TRADE',
                'equity', 'VFY2', 100.0, 7, 'open'
            )
            """,
            (snap,),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('VFY', '2026-05-01', 100.0)"
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('VFY', '2026-05-10', 108.0)"
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('VFY2', '2026-05-01', 100.0)"
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('VFY2', '2026-05-10', 92.0)"
        )
    from datetime import datetime, timezone

    resolve_open_outcomes(
        cfg, tmp_db, now=datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc),
    )
    statuses = {
        r["resolution_status"]
        for r in query_all(
            "SELECT resolution_status FROM opportunity_outcomes ORDER BY candidate_key",
            db_path=tmp_db,
        )
    }
    assert "resolved_hit" in statuses
    assert "resolved_miss" in statuses


# ── 24. Radar surfaces recent track record summary ─────────────────────────
def check_radar_recent_track_record(tmp_db: Path) -> None:
    from analysis.radar import generate_daily_radar
    from config import load_config
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    now = "2026-05-14T12:00:00+00:00"
    with get_conn(tmp_db) as conn:
        conn.execute("DELETE FROM opportunity_outcomes")
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolved_at, exit_price, realized_return,
                resolution_status
            ) VALUES (
                'equity:REC_HIT', ?, 'POSSIBLE_TRADE', 'equity', 'H1', 100.0, 7,
                ?, 105.0, 0.05, 'resolved_hit'
            )
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolved_at, exit_price, realized_return,
                resolution_status
            ) VALUES (
                'equity:REC_MISS', ?, 'INVESTIGATE', 'equity', 'H2', 100.0, 7,
                ?, 95.0, -0.05, 'resolved_miss'
            )
            """,
            (now, now),
        )
    text = generate_daily_radar(cfg, db_path=tmp_db, dry_run=True)
    assert "## Recent track record" in text
    assert "hit 1" in text
    assert "miss 1" in text


# ── 25. Dialogue context builder returns all 9 required keys ───────────────
def check_dialogue_context_keys(tmp_db: Path) -> None:
    from analysis.dialogue import _build_context
    from storage.db import init_db

    init_db(tmp_db)
    ctx = _build_context({"theses": {}}, tmp_db)
    for key in (
        "as_of",
        "positions",
        "top_opportunities",
        "active_narratives",
        "recent_news",
        "recent_outcomes",
        "source_health_warnings",
        "theses",
        "last_radar",
    ):
        assert key in ctx, f"missing key {key}"
    assert isinstance(ctx["positions"], list)
    assert isinstance(ctx["last_radar"], str)


# ── 26. Dialogue ask dry_run skips API ─────────────────────────────────────
def check_dialogue_ask_dry_run(tmp_db: Path) -> None:
    from unittest.mock import patch

    from analysis.dialogue import ask
    from storage.db import init_db

    init_db(tmp_db)
    with patch("analysis.dialogue.anthropic.Anthropic") as mock_anthropic:
        result = ask("test?", {"theses": {}}, tmp_db, dry_run=True)
        assert mock_anthropic.call_count == 0
    assert result.answer == "[dry-run: no API call made]"
    assert result.prompt_tokens == 0


# ── 27. Dialogue ask calls API and returns answer ──────────────────────────
def check_dialogue_ask_mocked_api(tmp_db: Path) -> None:
    from unittest.mock import MagicMock, patch

    from analysis.dialogue import ask
    from storage.db import init_db

    init_db(tmp_db)
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="mocked answer")]
    mock_response.model = "gatto-test"
    mock_response.usage = MagicMock(input_tokens=50, output_tokens=25)

    with patch("analysis.dialogue.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        result = ask("test?", {"theses": {}}, tmp_db)
    assert result.answer == "mocked answer"
    assert result.completion_tokens == 25
    assert result.model == "gatto-test"


# ── 28. Macro ingest skips when FRED_API_KEY absent ────────────────────────
def check_macro_ingest_skips_without_key(tmp_db: Path) -> None:
    import os

    from ingestion.macro import ingest_macro
    from storage.db import init_db, query_one

    init_db(tmp_db)
    saved = os.environ.pop("FRED_API_KEY", None)
    try:
        result = ingest_macro({}, tmp_db)
        assert result.skipped is True, "expected skipped when FRED_API_KEY absent"
        assert result.rows_upserted == 0
        row = query_one("SELECT COUNT(*) AS n FROM macro", db_path=tmp_db)
        assert row["n"] == 0
    finally:
        if saved is not None:
            os.environ["FRED_API_KEY"] = saved


# ── 29. Macro ingest upserts rows with mocked FRED ─────────────────────────
def check_macro_ingest_upserts_mocked(tmp_db: Path) -> None:
    import os
    from unittest.mock import patch

    import pandas as pd

    from ingestion.macro import ingest_macro
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    saved = os.environ.get("FRED_API_KEY")
    os.environ["FRED_API_KEY"] = "verify_test"
    try:
        with patch("fredapi.Fred") as mock_fred_cls:
            mock_fred = mock_fred_cls.return_value
            mock_fred.get_series.return_value = pd.Series(
                {
                    pd.Timestamp("2026-01-10"): 4.5,
                    pd.Timestamp("2026-01-11"): 4.6,
                }
            )
            result = ingest_macro(
                {"fred": {"series_map": {"DGS10": "10Y"}}},
                tmp_db,
            )
        assert result.rows_upserted == 2
        assert result.series_succeeded == 1
        with get_conn(tmp_db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM macro WHERE indicator='DGS10'"
            ).fetchone()[0]
        assert count == 2
    finally:
        if saved is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = saved


# ── 30. Dialogue context macro_snapshot from macro table ───────────────────
def check_dialogue_macro_snapshot(tmp_db: Path) -> None:
    from analysis.dialogue import _build_context
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO macro (indicator, date, value) VALUES (?, ?, ?)",
            ("UNRATE", "2026-01-15", 4.1),
        )
    ctx = _build_context({"theses": {}}, tmp_db)
    assert "macro_snapshot" in ctx
    unrate = next(
        (r for r in ctx["macro_snapshot"] if r["indicator"] == "UNRATE"),
        None,
    )
    assert unrate is not None, "UNRATE row missing from macro_snapshot"
    assert unrate["value"] == 4.1


# ── 31. Macro signals for oil group — WTI bullish ─────────────────────────
def check_macro_signals_wti_bullish() -> None:
    from analysis.opportunities import (
        _DEFAULT_MACRO_SIGNALS_CFG,
        _macro_signals_for_ticker,
    )

    result = _macro_signals_for_ticker(
        {"oil"},
        {"DCOILWTICO": {"value": 85.0, "change": 3.5}},
        _DEFAULT_MACRO_SIGNALS_CFG,
    )
    assert "wti_momentum_bullish" in result
    assert result


# ── 32. Macro signals empty when macro dict empty ──────────────────────────
def check_macro_signals_empty_when_no_macro() -> None:
    from analysis.opportunities import (
        _DEFAULT_MACRO_SIGNALS_CFG,
        _macro_signals_for_category,
        _macro_signals_for_ticker,
    )

    assert _macro_signals_for_ticker(
        {"oil", "fertilizer"}, {}, _DEFAULT_MACRO_SIGNALS_CFG,
    ) == []
    assert _macro_signals_for_category(
        "energy", {}, _DEFAULT_MACRO_SIGNALS_CFG,
    ) == []


# ── 33. Equity candidate evidence includes macro when rows seeded ──────────
def check_equity_evidence_includes_macro(tmp_db: Path) -> None:
    import json
    from datetime import datetime, timezone

    from analysis.opportunities import score_opportunities
    from storage.db import get_conn, init_db

    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    config = {
        "portfolio": {"positions": [], "prediction_markets": []},
        "theses": {},
        "watchlist": {"oil_tankers": ["TST"]},
        "news_sources": {"tier_1": []},
        "alerts": {},
        "llm": {},
        "schedule": {},
    }
    with get_conn(tmp_db) as conn:
        conn.executemany(
            "INSERT INTO macro (indicator, date, value) VALUES (?, ?, ?)",
            [
                ("DCOILWTICO", "2025-12-31", 78.5),
                ("DCOILWTICO", "2026-01-01", 82.0),
            ],
        )
        conn.execute(
            """
            INSERT INTO prices (ticker, date, close, pct_change, pct_change_5d)
            VALUES ('TST', '2026-05-14', 20.0, 0.5, 0.8)
            """
        )
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                id, cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (99, 'tst1', 'Oil supply risk', '{}', '["oil"]',
                ?, ?, 4, 5.0, 6.0, 1.0, 1.0, 'active', '["TST"]', '[]', ?)
            """,
            (now, now, now),
        )
    score_opportunities(config, tmp_db)
    with get_conn(tmp_db) as conn:
        row = conn.execute(
            "SELECT evidence FROM opportunity_candidates WHERE candidate_key = 'equity:TST'"
        ).fetchone()
    assert row is not None
    evidence = json.loads(row["evidence"])
    assert "macro" in evidence
    assert "wti_momentum_bullish" in evidence["macro"]["signals"]


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"Gatto Farioli verify — project at {PROJECT_DIR}\n")

    with tempfile.TemporaryDirectory(prefix="gatto-verify-") as tmpdir:
        tmp_db = Path(tmpdir) / "verify.db"

        _check("config loads", check_config_loads)
        _check("db initializes (every table present)", lambda: check_db_initializes(tmp_db))
        _check("news url normalization dedupes tracking params", check_url_normalization)
        _check("brief generation does not crash on empty db", lambda: check_brief_on_empty_db(tmp_db))
        _check("--health prints expected sections", lambda: check_health_runs(tmp_db))
        _check("thesis resolver handles ticker/uncertain patterns", lambda: check_thesis_signal_resolver(tmp_db))
        _check("init_db idempotent for Phase A–C tables", lambda: check_init_db_idempotent(tmp_db))
        _check("narrative clustering merges repeated headlines", lambda: check_narrative_clustering(tmp_db))
        _check("narrative status values (emerging/active/fading/resolved)", lambda: check_narrative_status_values(tmp_db))
        _check("kalshi categorizer maps known buckets", check_kalshi_categorizer)
        _check("kalshi sports excluded by default config", check_kalshi_sports_excluded_by_default)
        _check("source_health surfaces unhealthy sources", lambda: check_source_health(tmp_db))
        _check("opportunity upsert updates last_seen not duplicate", lambda: check_opportunity_upsert_idempotent(tmp_db))
        _check("opportunity action guards block weak POSSIBLE_TRADE", lambda: check_opportunity_action_guards(tmp_db))
        _check("news scoring writes importance + sectors", lambda: check_news_scoring(tmp_db))
        _check("radar generation does not crash on empty db", lambda: check_radar_on_empty_db(tmp_db))
        _check(
            "radar separates POSSIBLE_TRADE from WATCH/AVOID",
            lambda: check_radar_separates_possible_trade_from_watch(tmp_db),
        )
        _check("polymarket categorizer maps known buckets", check_polymarket_categorizer)
        _check("polymarket sports excluded by default config", check_polymarket_sports_excluded_by_default)
        _check(
            "quality bar downgrades POSSIBLE_TRADE to WATCH when invalidation missing",
            lambda: check_quality_bar_downgrades_possible_trade(tmp_db),
        )
        _check(
            "radar surfaces quality bar fields and exceptions",
            lambda: check_radar_quality_bar_surface(tmp_db),
        )
        _check(
            "outcomes snapshot creates row for POSSIBLE_TRADE candidate",
            lambda: check_outcomes_snapshot_creates_row(tmp_db),
        )
        _check(
            "outcomes resolve classifies hit vs miss correctly",
            lambda: check_outcomes_resolve_hit_miss(tmp_db),
        )
        _check(
            "radar surfaces recent track record summary",
            lambda: check_radar_recent_track_record(tmp_db),
        )
        _check(
            "dialogue context builder returns all 9 required keys",
            lambda: check_dialogue_context_keys(tmp_db),
        )
        _check(
            "dialogue ask dry_run returns result without calling API",
            lambda: check_dialogue_ask_dry_run(tmp_db),
        )
        _check(
            "dialogue ask calls Anthropic API and returns answer",
            lambda: check_dialogue_ask_mocked_api(tmp_db),
        )
        _check(
            "macro ingest skips cleanly when FRED_API_KEY absent",
            lambda: check_macro_ingest_skips_without_key(tmp_db),
        )
        _check(
            "macro ingest upserts rows with mocked FRED",
            lambda: check_macro_ingest_upserts_mocked(tmp_db),
        )
        _check(
            "dialogue context macro_snapshot populated from macro table",
            lambda: check_dialogue_macro_snapshot(tmp_db),
        )
        _check(
            "macro_signals_for_ticker returns wti_momentum_bullish for oil group",
            check_macro_signals_wti_bullish,
        )
        _check(
            "macro_signals_for_ticker returns empty list when macro dict empty",
            check_macro_signals_empty_when_no_macro,
        )
        _check(
            "equity candidate evidence includes macro key when macro rows seeded",
            lambda: check_equity_evidence_includes_macro(tmp_db),
        )

    total = len(PASSED) + len(FAILED)
    if total == 33 and not FAILED:
        print("\nVerify: 33/33 passed.")
    else:
        print(f"\nVerify: {len(PASSED)}/{total} passed.")
    if FAILED:
        print("Failures:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
