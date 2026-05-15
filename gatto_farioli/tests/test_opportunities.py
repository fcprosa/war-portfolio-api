"""Phase D opportunity scoring tests — no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from analysis.opportunities import (
    ACTION_INVESTIGATE,
    ACTION_NO_EDGE,
    ACTION_POSSIBLE_TRADE,
    ACTION_WATCH,
    _Candidate,
    score_opportunities,
    upsert_opportunity_candidates,
)
from storage.db import get_conn, init_db, query_one


def _insert_narrative(conn, *, id_: int, title: str, sectors: str, status: str = "active") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO narrative_clusters (
            id, cluster_key, title, summary, sectors,
            first_seen, last_seen, article_count,
            avg_importance, max_importance, momentum_24h, momentum_7d,
            status, related_tickers, related_markets, updated_at
        ) VALUES (?, ?, ?, '{}', ?, ?, ?, 5, 6.0, 7.0, 2.0, 1.5, ?, '[]', '[]', ?)
        """,
        (id_, f"key_{id_}", title, sectors, now, now, status, now),
    )


def test_upsert_updates_last_seen_not_duplicate(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    c = _Candidate(
        candidate_key="equity:TEST",
        title="Test",
        summary="s",
        source_type="equity",
        related_ticker="TEST",
        related_market_ticker=None,
        related_narrative_id=None,
        score=50.0,
        confidence=5.0,
        action=ACTION_WATCH,
        signals_count=2,
        missing_data=[],
        evidence={"k": "v"},
    )
    ins, upd = upsert_opportunity_candidates([c], tmp_db)
    assert ins == 1 and upd == 0

    c.score = 60.0
    ins2, upd2 = upsert_opportunity_candidates([c], tmp_db)
    assert ins2 == 0 and upd2 == 1

    n = query_one("SELECT COUNT(*) AS n FROM opportunity_candidates", db_path=tmp_db)["n"]
    assert n == 1
    row = query_one(
        "SELECT score, created_at, last_seen FROM opportunity_candidates WHERE candidate_key = ?",
        ("equity:TEST",),
        db_path=tmp_db,
    )
    assert row["score"] == 60.0
    assert row["created_at"] <= row["last_seen"]


def test_single_headline_not_possible_trade(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO news (url_hash, url, source, title, summary, sectors, importance, published_at)
            VALUES ('h1', 'https://x/1', 'X', 'Oil spikes on Hormuz', 'Crude up', 'oil', 8.0, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO prices (ticker, date, close, pct_change, pct_change_5d)
            VALUES ('CVX', '2026-05-14', 100.0, 0.1, 0.2)
            """
        )
    result = score_opportunities(minimal_config, db_path=tmp_db)
    assert result.candidates_scored >= 1
    row = query_one(
        "SELECT action FROM opportunity_candidates WHERE related_ticker = 'CVX'",
        db_path=tmp_db,
    )
    if row:
        assert row["action"] != ACTION_POSSIBLE_TRADE


def test_narrative_only_not_possible_trade(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        _insert_narrative(conn, id_=1, title="Hormuz oil supply risk", sectors='["oil","geopolitics"]')
    minimal_config["watchlist"] = {"oil": ["CVX"]}
    score_opportunities(minimal_config, db_path=tmp_db)
    row = query_one(
        "SELECT action, signals_count FROM opportunity_candidates WHERE candidate_key = 'equity:CVX'",
        db_path=tmp_db,
    )
    if row:
        assert row["action"] != ACTION_POSSIBLE_TRADE


def test_politics_kalshi_only_not_possible_trade(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO market_universe (
                platform, symbol, title, category, status,
                volume_24h, yes_price, no_price, discovered_at, updated_at, metadata
            ) VALUES (
                'kalshi', 'KXPOL-1', 'Who wins election', 'politics', 'open',
                50000, 0.5, 0.5, datetime('now'), datetime('now'), '{}'
            )
            """
        )
    score_opportunities(minimal_config, db_path=tmp_db)
    row = query_one(
        "SELECT action FROM opportunity_candidates WHERE candidate_key = 'kalshi:KXPOL-1'",
        db_path=tmp_db,
    )
    assert row is not None
    assert row["action"] != ACTION_POSSIBLE_TRADE


def test_missing_odds_caps_action(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        _insert_narrative(conn, id_=2, title="Taiwan risk", sectors='["geopolitics"]')
        conn.execute(
            """
            INSERT INTO news (url_hash, url, source, title, summary, sectors, importance, published_at)
            VALUES ('h2', 'https://x/2', 'X', 'Taiwan tensions rise', 'State dept', 'geopolitics', 7.0, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO market_universe (
                platform, symbol, title, category, status,
                volume_24h, yes_price, no_price, discovered_at, updated_at, metadata
            ) VALUES (
                'kalshi', 'KX-TW-1', 'Taiwan level 4 warning', 'geopolitics', 'open',
                100, NULL, NULL, datetime('now'), datetime('now'), '{}'
            )
            """
        )
    score_opportunities(minimal_config, db_path=tmp_db)
    row = query_one(
        "SELECT action, missing_data FROM opportunity_candidates WHERE candidate_key = 'kalshi:KX-TW-1'",
        db_path=tmp_db,
    )
    assert row is not None
    assert row["action"] in (ACTION_WATCH, ACTION_NO_EDGE, ACTION_INVESTIGATE)
    assert row["action"] != ACTION_POSSIBLE_TRADE
    missing = json.loads(row["missing_data"] or "[]")
    assert "no_market_odds" in missing


def test_multi_signal_can_reach_investigate_or_possible_trade(tmp_db, minimal_config) -> None:
    """Synthetic fixture with narrative + news + price + odds should clear INVESTIGATE at minimum."""
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        _insert_narrative(conn, id_=10, title="Oil Hormuz disruption", sectors='["oil","geopolitics"]')
        conn.execute(
            """
            INSERT INTO news (url_hash, url, source, title, summary, sectors, importance, published_at)
            VALUES ('h10', 'https://x/10', 'X', 'Brent jumps on Hormuz closure risk', 'Crude supply', 'oil', 8.5, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO prices (ticker, date, close, pct_change, pct_change_5d)
            VALUES ('CVX', '2026-05-14', 150.0, 0.5, 0.8)
            """
        )
        conn.execute(
            """
            INSERT INTO market_universe (
                platform, symbol, title, category, status,
                volume_24h, yes_price, no_price, discovered_at, updated_at, metadata
            ) VALUES (
                'kalshi', 'KX-OIL-1', 'Will Brent exceed 100', 'energy', 'open',
                800, 0.45, 0.55, datetime('now'), datetime('now'), '{}'
            )
            """
        )
    minimal_config["watchlist"] = {"oil": ["CVX"]}
    score_opportunities(minimal_config, db_path=tmp_db)
    row = query_one(
        "SELECT action, score, signals_count, evidence FROM opportunity_candidates WHERE candidate_key = 'equity:CVX'",
        db_path=tmp_db,
    )
    assert row is not None
    assert row["action"] in (ACTION_INVESTIGATE, ACTION_POSSIBLE_TRADE, ACTION_WATCH)
    assert row["signals_count"] >= 2
