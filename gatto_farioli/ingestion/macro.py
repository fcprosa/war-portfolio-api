"""Macro data ingestion via FRED (Federal Reserve Economic Data)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from storage import source_health
from storage.db import get_conn

_DEFAULT_FRED_SERIES: dict[str, str] = {
    # Rates
    "DFF": "Fed Funds Effective Rate",
    "DGS10": "10-Year Treasury Yield",
    "DGS2": "2-Year Treasury Yield",
    "T10Y2Y": "10Y-2Y Yield Curve Spread",
    # Inflation
    "CPIAUCSL": "CPI All Items",
    "CPILFESL": "Core CPI (ex food & energy)",
    "T5YIE": "5-Year Breakeven Inflation",
    # Growth / Labor
    "UNRATE": "Unemployment Rate",
    "ICSA": "Initial Jobless Claims (weekly)",
    # Commodities
    "DCOILWTICO": "WTI Crude Oil Price",
    "DCOILBRENTEU": "Brent Crude Oil Price",
    # Dollar
    "DTWEXBGS": "Trade-Weighted US Dollar Index",
    # Credit
    "BAMLH0A0HYM2": "HY Credit Spread (OAS)",
}

_DEFAULT_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class MacroIngestResult:
    series_attempted: int
    series_succeeded: int
    rows_upserted: int
    failures: list[dict]
    skipped: bool = False
    skip_reason: str = ""


def ingest_macro(
    config: dict[str, Any],
    db_path: str | Path,
    *,
    dry_run: bool = False,
) -> MacroIngestResult:
    fred_cfg = config.get("fred", {})
    lookback_days = int(fred_cfg.get("lookback_days", _DEFAULT_LOOKBACK_DAYS))
    series_map = fred_cfg.get("series_map", _DEFAULT_FRED_SERIES)
    if isinstance(series_map, list):
        series_map = {s: s for s in series_map}

    load_dotenv()
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return MacroIngestResult(
            series_attempted=0,
            series_succeeded=0,
            rows_upserted=0,
            failures=[],
            skipped=True,
            skip_reason="FRED_API_KEY not set",
        )

    start_date = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")

    from fredapi import Fred

    fred = Fred(api_key=api_key)
    failures: list[dict] = []
    total_rows = 0

    for series_id, label in series_map.items():
        try:
            data = fred.get_series(series_id, observation_start=start_date)
            data = data.dropna()
            rows: list[tuple[str, str, float]] = []
            for idx, value in data.items():
                date_str = idx.strftime("%Y-%m-%d")
                rows.append((series_id, date_str, float(value)))

            if not dry_run:
                if rows:
                    with get_conn(db_path) as conn:
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO macro (indicator, date, value)
                            VALUES (?, ?, ?)
                            """,
                            rows,
                        )
                total_rows += len(rows)
                source_health.record_success(
                    f"fred:{series_id}",
                    f"{len(rows)} rows",
                    db_path=db_path,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {"series": series_id, "label": label, "error": str(exc)},
            )
            if not dry_run:
                source_health.record_failure(
                    f"fred:{series_id}",
                    str(exc),
                    db_path=db_path,
                )

    return MacroIngestResult(
        series_attempted=len(series_map),
        series_succeeded=len(series_map) - len(failures),
        rows_upserted=total_rows,
        failures=failures,
    )
