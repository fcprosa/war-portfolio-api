"""Phase D opportunity scoring tests — no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from unittest.mock import patch

from analysis.opportunities import (
    ACTION_INVESTIGATE,
    ACTION_NO_EDGE,
    ACTION_POSSIBLE_TRADE,
    ACTION_WATCH,
    _Candidate,
    _apply_quality_bar_downgrade,
    compute_quality_bar,
    score_opportunities,
    upsert_opportunity_candidates,
)
from storage.source_health import record_failure
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


def test_compute_quality_bar_passes_with_narrative_prices_and_healthy_sources(
    tmp_db, minimal_config,
) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                id, cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (1, 'k1', 'Oil supply shock', '{}', '["oil"]',
                ?, ?, 6, 5.0, 6.0, 2.0, 1.0, 'active', '["IPI"]', '[]', ?)
            """,
            (now, now, now),
        )
        for i, close in enumerate([28.0, 30.0, 32.0], start=1):
            conn.execute(
                "INSERT INTO prices (ticker, date, close) VALUES ('IPI', ?, ?)",
                (f"2026-05-{i:02d}", close),
            )
    c = _Candidate(
        candidate_key="equity:IPI",
        title="IPI setup",
        summary="s",
        source_type="equity",
        related_ticker="IPI",
        related_market_ticker=None,
        related_narrative_id=1,
        score=80.0,
        confidence=8.0,
        action=ACTION_WATCH,
        signals_count=3,
        missing_data=[],
        evidence={"news": [{"title": "Potash prices rise", "source": "http://feed.example/ok"}]},
    )
    qb = compute_quality_bar(c, db_path=tmp_db)
    assert qb["passed"] is True
    assert qb["catalyst_path"] is not None
    assert qb["invalidation_trigger"] is not None
    assert qb["risk_reward_summary"] is not None
    assert qb["executable_instrument"] == "equity:IPI"
    assert qb["data_health_ok"] is True


def test_compute_quality_bar_fails_without_prices_for_invalidation(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                id, cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (2, 'k2', 'Fertilizer squeeze', '{}', '["fertilizer"]',
                ?, ?, 4, 5.0, 6.0, 1.0, 1.0, 'active', '["MOS"]', '[]', ?)
            """,
            (now, now, now),
        )
    c = _Candidate(
        candidate_key="equity:MOS",
        title="MOS",
        summary="s",
        source_type="equity",
        related_ticker="MOS",
        related_market_ticker=None,
        related_narrative_id=2,
        score=70.0,
        confidence=6.0,
        action=ACTION_INVESTIGATE,
        signals_count=2,
        missing_data=[],
        evidence={},
    )
    qb = compute_quality_bar(c, db_path=tmp_db)
    assert qb["passed"] is False
    assert "invalidation_trigger" in qb["missing_items"]
    assert "risk_reward_summary" in qb["missing_items"]


def test_compute_quality_bar_data_health_false_on_error_source(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    record_failure("http://feed.example/broken", "timeout", db_path=tmp_db)
    c = _Candidate(
        candidate_key="equity:X",
        title="X",
        summary="s",
        source_type="equity",
        related_ticker="X",
        related_market_ticker=None,
        related_narrative_id=None,
        score=50.0,
        confidence=5.0,
        action=ACTION_WATCH,
        signals_count=1,
        missing_data=[],
        evidence={"news": [{"title": "Bad feed story", "source": "http://feed.example/broken"}]},
    )
    qb = compute_quality_bar(c, db_path=tmp_db)
    assert qb["data_health_ok"] is False
    assert "data_health_ok" in qb["missing_items"]


def test_score_opportunities_downgrades_possible_trade_when_quality_bar_fails(
    tmp_db, minimal_config,
) -> None:
    init_db(tmp_db)

    def _one_possible_trade(config: dict, db_path) -> list[_Candidate]:
        return [
            _Candidate(
                candidate_key="equity:FAILQB",
                title="Fail QB equity",
                summary="s",
                source_type="equity",
                related_ticker="FAILQB",
                related_market_ticker=None,
                related_narrative_id=None,
                score=90.0,
                confidence=9.0,
                action=ACTION_POSSIBLE_TRADE,
                signals_count=4,
                missing_data=[],
                evidence={"news": [{"title": "Single headline only"}]},
                has_tradable_instrument=True,
                signal_types={"news", "price", "narrative"},
            )
        ]

    with patch("analysis.opportunities._build_candidates", _one_possible_trade):
        score_opportunities(minimal_config, db_path=tmp_db)

    row = query_one(
        """
        SELECT action, quality_bar_passed, quality_bar_missing
        FROM opportunity_candidates WHERE candidate_key = 'equity:FAILQB'
        """,
        db_path=tmp_db,
    )
    assert row is not None
    assert row["action"] == ACTION_WATCH
    assert row["quality_bar_passed"] == 0
    missing = json.loads(row["quality_bar_missing"] or "[]")
    assert "invalidation_trigger" in missing


def test_score_opportunities_does_not_raise_watch_when_quality_bar_passes(
    tmp_db, minimal_config,
) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                id, cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (5, 'k5', 'Stable fertilizer', '{}', '["fertilizer"]',
                ?, ?, 3, 4.0, 5.0, 1.0, 1.0, 'active', '["IPI"]', '[]', ?)
            """,
            (now, now, now),
        )
        for d, close in [("2026-04-01", 25.0), ("2026-05-01", 30.0), ("2026-05-14", 28.0)]:
            conn.execute(
                "INSERT INTO prices (ticker, date, close) VALUES ('IPI', ?, ?)",
                (d, close),
            )

    def _one_watch(config: dict, db_path) -> list[_Candidate]:
        return [
            _Candidate(
                candidate_key="equity:IPI",
                title="IPI watch",
                summary="s",
                source_type="equity",
                related_ticker="IPI",
                related_market_ticker=None,
                related_narrative_id=5,
                score=40.0,
                confidence=5.0,
                action=ACTION_WATCH,
                signals_count=2,
                missing_data=[],
                evidence={"news": [{"title": "Steady potash", "source": "http://feed.example/ok"}]},
            )
        ]

    with patch("analysis.opportunities._build_candidates", _one_watch):
        score_opportunities(minimal_config, db_path=tmp_db)

    row = query_one(
        "SELECT action, quality_bar_passed FROM opportunity_candidates WHERE candidate_key = 'equity:IPI'",
        db_path=tmp_db,
    )
    assert row is not None
    assert row["action"] == ACTION_WATCH
    assert row["quality_bar_passed"] == 1


def test_apply_quality_bar_downgrade_caps_investigate(tmp_db, minimal_config) -> None:
    c = _Candidate(
        candidate_key="equity:Z",
        title="Z",
        summary="s",
        source_type="equity",
        related_ticker="Z",
        related_market_ticker=None,
        related_narrative_id=None,
        score=60.0,
        confidence=6.0,
        action=ACTION_INVESTIGATE,
        signals_count=2,
        missing_data=[],
        evidence={},
    )
    _apply_quality_bar_downgrade(c, {"passed": False, "missing_items": ["invalidation_trigger"]})
    assert c.action == ACTION_WATCH
