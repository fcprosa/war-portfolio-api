"""Phase J macro ingestion tests — offline, mocked FRED API."""

from __future__ import annotations

import os
from unittest.mock import ANY, MagicMock, patch

import pandas as pd

from analysis.dialogue import _build_context
from ingestion.macro import ingest_macro
from storage.db import get_conn, init_db, query_one


def test_ingest_macro_skips_when_no_api_key(tmp_db) -> None:
    saved = os.environ.pop("FRED_API_KEY", None)
    try:
        init_db(tmp_db)
        result = ingest_macro({}, tmp_db)
        assert result.skipped is True
        assert result.rows_upserted == 0
        assert "FRED_API_KEY" in result.skip_reason
    finally:
        if saved is not None:
            os.environ["FRED_API_KEY"] = saved


@patch("fredapi.Fred")
def test_ingest_macro_upserts_rows(mock_fred_cls, tmp_db) -> None:
    saved = os.environ.get("FRED_API_KEY")
    os.environ["FRED_API_KEY"] = "test"
    try:
        init_db(tmp_db)
        mock_fred = MagicMock()
        mock_fred_cls.return_value = mock_fred
        mock_fred.get_series.return_value = pd.Series(
            {
                pd.Timestamp("2026-01-01"): 5.33,
                pd.Timestamp("2026-01-02"): 5.34,
            }
        )

        result = ingest_macro(
            {"fred": {"series_map": {"DFF": "Fed Funds"}}},
            tmp_db,
        )
        assert result.rows_upserted == 2
        assert result.series_succeeded == 1
        mock_fred.get_series.assert_called_once_with("DFF", observation_start=ANY)

        row = query_one(
            "SELECT COUNT(*) AS n FROM macro WHERE indicator='DFF'",
            db_path=tmp_db,
        )
        assert row["n"] == 2
    finally:
        if saved is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = saved


@patch("fredapi.Fred")
def test_ingest_macro_records_failure_and_continues(mock_fred_cls, tmp_db) -> None:
    saved = os.environ.get("FRED_API_KEY")
    os.environ["FRED_API_KEY"] = "test"
    try:
        init_db(tmp_db)
        mock_fred = MagicMock()
        mock_fred_cls.return_value = mock_fred

        def side_effect(series_id: str, observation_start: str) -> pd.Series:
            if series_id == "FAIL":
                raise Exception("timeout")
            return pd.Series({pd.Timestamp("2026-01-01"): 4.5})

        mock_fred.get_series.side_effect = side_effect

        result = ingest_macro(
            {
                "fred": {
                    "series_map": {
                        "FAIL": "Fails",
                        "DGS10": "10Y",
                    }
                }
            },
            tmp_db,
        )
        assert len(result.failures) == 1
        assert result.series_succeeded == 1
        row = query_one(
            "SELECT COUNT(*) AS n FROM macro WHERE indicator='DGS10'",
            db_path=tmp_db,
        )
        assert row["n"] == 1
    finally:
        if saved is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = saved


@patch("fredapi.Fred")
def test_ingest_macro_dry_run_writes_nothing(mock_fred_cls, tmp_db) -> None:
    saved = os.environ.get("FRED_API_KEY")
    os.environ["FRED_API_KEY"] = "test"
    try:
        init_db(tmp_db)
        mock_fred = MagicMock()
        mock_fred_cls.return_value = mock_fred
        mock_fred.get_series.return_value = pd.Series(
            {pd.Timestamp("2026-01-01"): 5.0}
        )

        result = ingest_macro(
            {"fred": {"series_map": {"DFF": "Fed Funds"}}},
            tmp_db,
            dry_run=True,
        )
        assert result.rows_upserted == 0
        row = query_one("SELECT COUNT(*) AS n FROM macro", db_path=tmp_db)
        assert row["n"] == 0
    finally:
        if saved is None:
            os.environ.pop("FRED_API_KEY", None)
        else:
            os.environ["FRED_API_KEY"] = saved


def test_build_context_includes_macro_snapshot(tmp_db) -> None:
    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        conn.executemany(
            "INSERT INTO macro (indicator, date, value) VALUES (?, ?, ?)",
            [
                ("DFF", "2026-01-01", 5.33),
                ("DFF", "2026-01-02", 5.34),
            ],
        )

    ctx = _build_context({"theses": {}}, tmp_db)
    assert "macro_snapshot" in ctx
    assert len(ctx["macro_snapshot"]) == 1
    assert ctx["macro_snapshot"][0]["indicator"] == "DFF"
    assert ctx["macro_snapshot"][0]["value"] == 5.34


def test_build_context_macro_snapshot_empty_when_no_rows(tmp_db) -> None:
    init_db(tmp_db)
    ctx = _build_context({"theses": {}}, tmp_db)
    assert ctx["macro_snapshot"] == []
