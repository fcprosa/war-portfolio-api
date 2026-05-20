"""Phase E daily radar tests — no network."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from analysis.opportunities import ACTION_AVOID, ACTION_POSSIBLE_TRADE, ACTION_WATCH
from analysis.radar import RADAR_TYPE, generate_daily_radar
from storage.db import get_conn, init_db, query_one


def test_radar_header_includes_utc_timestamp_and_type_label(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert RADAR_TYPE in text
    assert "edge_radar_v1" in text
    assert re.search(r"Generated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text)


def test_empty_db_renders_all_section_headers(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    for header in (
        "## Top opportunities",
        "## Active & emerging narratives",
        "## Position-aware callouts",
        "## Quality bar exceptions",
        "## Recent track record",
        "## Source-health warnings",
        "## Missing-data flags",
    ):
        assert header in text


def test_top_opportunities_respects_top_n_and_order(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    minimal_config["radar"] = {"top_n": 2, "staleness_hours": 36, "narrative_max": 8}
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.executemany(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status
            ) VALUES (?, ?, '', 'equity', ?, ?, ?, 1, '[]', '{}', ?, ?, 'open')
            """,
            [
                ("equity:LOW", "Low score row", 10.0, 1.0, ACTION_WATCH, now, now),
                ("equity:MID", "Mid score row", 50.0, 5.0, ACTION_WATCH, now, now),
                ("equity:HIGH", "High score row", 90.0, 9.0, ACTION_WATCH, now, now),
            ],
        )

    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert "High score row" in text
    assert "Mid score row" in text
    assert "Low score row" not in text
    assert text.index("High score row") < text.index("Mid score row")


def test_possible_trade_appears_before_watch_and_avoid(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.executemany(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status
            ) VALUES (?, ?, '', 'equity', ?, ?, ?, 1, '[]', '{}', ?, ?, 'open')
            """,
            [
                ("equity:AVD", "Avoid setup name", 40.0, 4.0, ACTION_AVOID, now, now),
                ("equity:WCH", "Watch setup name", 60.0, 6.0, ACTION_WATCH, now, now),
                ("equity:PTD", "Possible trade setup name", 95.0, 9.0, ACTION_POSSIBLE_TRADE, now, now),
            ],
        )

    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert text.index("Possible trade setup name") < text.index("Watch setup name")
    assert text.index("Watch setup name") < text.index("Avoid setup name")


def test_position_narrative_callout_when_related_ticker_matches(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO positions (ticker, shares, avg_cost, thesis, conviction, last_updated)
            VALUES ('IPI', 10, 30.0, 'fertilizer thesis', 8, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO narrative_clusters (
                cluster_key, title, summary, sectors,
                first_seen, last_seen, article_count,
                avg_importance, max_importance, momentum_24h, momentum_7d,
                status, related_tickers, related_markets, updated_at
            ) VALUES (
                'nar_ipi', 'Potash supply shock narrative', '{}', '["fertilizer"]',
                ?, ?, 8, 6.0, 7.0, 2.5, 1.0, 'active',
                ?, '[]', ?
            )
            """,
            (now, now, json.dumps(["IPI"]), now),
        )

    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert "### IPI" in text
    assert "Potash supply shock narrative" in text


def test_dry_run_does_not_write_brief_or_runs(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert query_one("SELECT COUNT(*) AS n FROM briefs", db_path=tmp_db)["n"] == 0
    assert query_one("SELECT COUNT(*) AS n FROM runs WHERE module = 'radar'", db_path=tmp_db)["n"] == 0


def test_persist_writes_brief_and_runs_row(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=False)
    brief = query_one(
        "SELECT type FROM briefs WHERE type = ?",
        (RADAR_TYPE,),
        db_path=tmp_db,
    )
    assert brief is not None
    run = query_one("SELECT status, message FROM runs WHERE module = 'radar'", db_path=tmp_db)
    assert run is not None
    assert run["status"] == "ok"
    assert "edge_radar_v1" in run["message"]


def test_radar_includes_quality_bar_sub_lines(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status,
                catalyst_path, invalidation_trigger, risk_reward_summary,
                quality_bar_passed, quality_bar_missing
            ) VALUES (
                'equity:QB1', 'QB Rich Row', '', 'equity', 88.0, 8.0, 'WATCH', 3,
                '[]', '{}', ?, ?, 'open',
                'Oil narrative catalyst', 'close outside [20, 40] (30d band on QB1)',
                '+10% / -5% (30d band)', 1, '[]'
            )
            """,
            (now, now),
        )
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert "catalyst: Oil narrative catalyst" in text
    assert "invalidate if: close outside [20, 40]" in text
    assert "R/R: +10% / -5% (30d band)" in text
    assert "QB: PASS" in text


def test_radar_quality_bar_exceptions_section(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status,
                catalyst_path, invalidation_trigger, risk_reward_summary,
                quality_bar_passed, quality_bar_missing
            ) VALUES (
                'equity:EXC', 'Downgraded High Score', '', 'equity', 72.0, 7.0, 'WATCH', 3,
                '[]', '{}', ?, ?, 'open',
                NULL, NULL, NULL, 0, '["invalidation_trigger","risk_reward_summary"]'
            )
            """,
            (now, now),
        )
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert "## Quality bar exceptions" in text
    assert "Downgraded High Score" in text
    assert "downgraded to WATCH" in text
    assert "invalidation_trigger" in text


def test_radar_quality_bar_exceptions_empty_shows_no_data(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    block = text.split("## Quality bar exceptions")[1].split("## Recent track record")[0]
    assert "_no data_" in block


def test_radar_recent_track_record_with_resolved_rows(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc)
    resolved_at = now.isoformat()
    snap = (now - timedelta(days=3)).isoformat()
    with get_conn(tmp_db) as conn:
        conn.executemany(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                score_at_emission, confidence_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolved_at, exit_price, realized_return,
                resolution_status
            ) VALUES (?, ?, 'POSSIBLE_TRADE', 80.0, 8.0, 'equity', 'TR1', 100.0, 7, ?, 106.0, 0.06, 'resolved_hit')
            """,
            [("equity:TR1", snap, resolved_at)],
        )
        conn.executemany(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                score_at_emission, confidence_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolved_at, exit_price, realized_return,
                resolution_status
            ) VALUES (?, ?, 'INVESTIGATE', 70.0, 7.0, 'equity', 'TR2', 100.0, 7, ?, 94.0, -0.06, 'resolved_miss')
            """,
            [("equity:TR2", snap, resolved_at)],
        )

    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    assert "## Recent track record" in text
    assert "hit 1" in text
    assert "miss 1" in text
    assert "equity:TR1" in text


def test_radar_recent_track_record_empty_shows_no_data(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    text = generate_daily_radar(minimal_config, db_path=tmp_db, dry_run=True)
    block = text.split("## Recent track record")[1].split("## Source-health warnings")[0]
    assert "_no data_" in block
