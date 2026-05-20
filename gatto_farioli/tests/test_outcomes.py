"""Phase H outcome tracking tests — no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analysis.outcomes import resolve_open_outcomes, snapshot_open_opportunities
from storage.db import get_conn, init_db, query_all, query_one


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def test_snapshot_inserts_row_for_possible_trade_with_price(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now = _iso(datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc))
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, related_ticker,
                score, confidence, action, signals_count, missing_data, evidence,
                created_at, last_seen, status
            ) VALUES (
                'equity:SNAP1', 'Snap test', '', 'equity', 'AAA',
                80.0, 8.0, 'POSSIBLE_TRADE', 3, '[]', '{}', ?, ?, 'open'
            )
            """,
            (now, now),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('AAA', '2026-05-14', 50.0)"
        )

    result = snapshot_open_opportunities(
        minimal_config, tmp_db, now=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert result.rows_created == 1
    row = query_one(
        "SELECT instrument_kind, entry_price FROM opportunity_outcomes WHERE candidate_key = 'equity:SNAP1'",
        db_path=tmp_db,
    )
    assert row is not None
    assert row["instrument_kind"] == "equity"
    assert row["entry_price"] == 50.0


def test_snapshot_idempotent_same_utc_day(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now_dt = datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc)
    now = _iso(now_dt)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, related_ticker,
                score, confidence, action, signals_count, missing_data, evidence,
                created_at, last_seen, status
            ) VALUES (
                'equity:IDEM', 'Idem', '', 'equity', 'BBB',
                70.0, 7.0, 'INVESTIGATE', 2, '[]', '{}', ?, ?, 'open'
            )
            """,
            (now, now),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('BBB', '2026-05-14', 10.0)"
        )

    r1 = snapshot_open_opportunities(minimal_config, tmp_db, now=now_dt)
    r2 = snapshot_open_opportunities(minimal_config, tmp_db, now=now_dt + timedelta(hours=2))
    assert r1.rows_created == 1
    assert r2.rows_created == 0
    assert r2.rows_skipped_existing == 1
    count = query_one("SELECT COUNT(*) AS n FROM opportunity_outcomes", db_path=tmp_db)
    assert count["n"] == 1


def test_snapshot_null_entry_price_with_notes_when_no_prices(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    now_dt = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    now = _iso(now_dt)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, related_ticker,
                score, confidence, action, signals_count, missing_data, evidence,
                created_at, last_seen, status
            ) VALUES (
                'equity:NOPX', 'No price', '', 'equity', 'ZZZ',
                75.0, 7.5, 'POSSIBLE_TRADE', 3, '[]', '{}', ?, ?, 'open'
            )
            """,
            (now, now),
        )

    result = snapshot_open_opportunities(minimal_config, tmp_db, now=now_dt)
    assert result.rows_created == 1
    assert result.rows_skipped_missing_price == 1
    row = query_one(
        "SELECT entry_price, notes FROM opportunity_outcomes WHERE candidate_key = 'equity:NOPX'",
        db_path=tmp_db,
    )
    assert row["entry_price"] is None
    assert "no equity price" in (row["notes"] or "")


def test_resolve_hit_at_equity_threshold(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    snap = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    resolve_at = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                score_at_emission, confidence_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:HIT', ?, 'POSSIBLE_TRADE', 80.0, 8.0,
                'equity', 'HIT', 100.0, 7, 'open'
            )
            """,
            (_iso(snap),),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('HIT', '2026-05-01', 100.0)"
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('HIT', '2026-05-10', 106.0)"
        )

    result = resolve_open_outcomes(minimal_config, tmp_db, now=resolve_at)
    assert result.rows_resolved_hit == 1
    row = query_one(
        "SELECT resolution_status, realized_return FROM opportunity_outcomes WHERE candidate_key = 'equity:HIT'",
        db_path=tmp_db,
    )
    assert row["resolution_status"] == "resolved_hit"
    assert row["realized_return"] == 0.06


def test_resolve_miss_at_negative_equity_threshold(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    snap = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    resolve_at = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                score_at_emission, confidence_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:MISS', ?, 'POSSIBLE_TRADE', 70.0, 7.0,
                'equity', 'MISS', 100.0, 7, 'open'
            )
            """,
            (_iso(snap),),
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('MISS', '2026-05-01', 100.0)"
        )
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES ('MISS', '2026-05-10', 94.0)"
        )

    result = resolve_open_outcomes(minimal_config, tmp_db, now=resolve_at)
    assert result.rows_resolved_miss == 1
    row = query_one(
        "SELECT resolution_status FROM opportunity_outcomes WHERE candidate_key = 'equity:MISS'",
        db_path=tmp_db,
    )
    assert row["resolution_status"] == "resolved_miss"


def test_resolve_unresolvable_when_entry_price_null(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    snap = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    resolve_at = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:UNR', ?, 'INVESTIGATE',
                'equity', 'UNR', NULL, 7, 'open'
            )
            """,
            (_iso(snap),),
        )

    result = resolve_open_outcomes(minimal_config, tmp_db, now=resolve_at)
    assert result.rows_resolved_unresolvable == 1
    row = query_one(
        "SELECT resolution_status, notes FROM opportunity_outcomes WHERE candidate_key = 'equity:UNR'",
        db_path=tmp_db,
    )
    assert row["resolution_status"] == "unresolvable"


def test_resolve_leaves_row_open_before_window_elapses(tmp_db, minimal_config) -> None:
    init_db(tmp_db)
    snap = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    early = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO opportunity_outcomes (
                candidate_key, snapshot_at, action_at_emission,
                instrument_kind, instrument_symbol, entry_price,
                resolution_window_days, resolution_status
            ) VALUES (
                'equity:OPEN', ?, 'POSSIBLE_TRADE',
                'equity', 'OPEN', 50.0, 7, 'open'
            )
            """,
            (_iso(snap),),
        )

    result = resolve_open_outcomes(minimal_config, tmp_db, now=early)
    assert result.rows_examined == 0
    row = query_one(
        "SELECT resolution_status FROM opportunity_outcomes WHERE candidate_key = 'equity:OPEN'",
        db_path=tmp_db,
    )
    assert row["resolution_status"] == "open"
