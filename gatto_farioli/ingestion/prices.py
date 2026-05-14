"""Price ingestion via yfinance.

Pulls ~35 calendar days of daily OHLCV history for every ticker in
``portfolio.positions`` and every group inside ``watchlist`` and upserts
the rows into ``prices`` keyed by ``(ticker, date)``. Computes 1d/5d/30d
percentage changes against the close column.

Designed to be resilient: bad tickers are logged and skipped, batch
download failure falls back to per-ticker, and the run still returns a
structured summary so ``run.py`` can record health without crashing.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from storage.db import DEFAULT_DB_PATH, get_conn

logger = logging.getLogger(__name__)

DEFAULT_PERIOD_DAYS = 35


@dataclass
class PriceIngestionResult:
    """Structured summary of one price ingestion run."""

    tickers_attempted: int
    tickers_succeeded: int
    rows_upserted: int
    failures: list[dict[str, str]] = field(default_factory=list)


def collect_tickers(config: dict[str, Any]) -> list[str]:
    """Return the deduped list of tickers to fetch, preserving config order."""
    seen: list[str] = []
    seen_set: set[str] = set()

    for pos in (config.get("portfolio", {}) or {}).get("positions", []) or []:
        ticker = (pos.get("ticker") or "").strip()
        if ticker and ticker not in seen_set:
            seen.append(ticker)
            seen_set.add(ticker)

    for _group, symbols in (config.get("watchlist", {}) or {}).items():
        for raw in symbols or []:
            sym = (raw or "").strip()
            if sym and sym not in seen_set:
                seen.append(sym)
                seen_set.add(sym)

    return seen


def _safe_float(value: Any) -> float | None:
    """Convert yfinance/pandas values to a plain Python float or None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _safe_int(value: Any) -> int | None:
    """Convert yfinance volume to int or None."""
    f = _safe_float(value)
    return int(f) if f is not None else None


def _rows_from_history(ticker: str, hist: pd.DataFrame) -> list[dict[str, Any]]:
    """Turn a yfinance history frame into upsert-ready row dicts."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return []
    hist = hist.dropna(subset=["Close"]).sort_index()
    if hist.empty:
        return []

    closes = [_safe_float(c) for c in hist["Close"].tolist()]
    rows: list[dict[str, Any]] = []
    for i, (idx, row) in enumerate(hist.iterrows()):
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]

        pct_1 = None
        if i >= 1 and closes[i - 1] not in (None, 0):
            pct_1 = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
        pct_5 = None
        if i >= 5 and closes[i - 5] not in (None, 0):
            pct_5 = (closes[i] - closes[i - 5]) / closes[i - 5] * 100.0
        pct_30 = None
        if i >= 30 and closes[i - 30] not in (None, 0):
            pct_30 = (closes[i] - closes[i - 30]) / closes[i - 30] * 100.0

        rows.append({
            "ticker": ticker,
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": closes[i],
            "volume": _safe_int(row.get("Volume")),
            "pct_change": pct_1,
            "pct_change_5d": pct_5,
            "pct_change_30d": pct_30,
        })
    return rows


def _batch_download(tickers: list[str], start: str) -> pd.DataFrame | None:
    """Try a single batched yfinance call. Returns None on failure."""
    if not tickers:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(
                tickers=tickers,
                start=start,
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
        if df is None or df.empty:
            return None
        return df
    except Exception as exc:  # noqa: BLE001 — yfinance raises many shapes
        logger.warning("[prices] batch download failed: %s", exc)
        return None


def _slice_batch(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Extract one ticker's frame from a group_by='ticker' batch result."""
    if df is None or df.empty:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if ticker not in df.columns.get_level_values(0):
                return None
            sub = df[ticker]
        else:
            sub = df
        if not isinstance(sub, pd.DataFrame) or sub.empty:
            return None
        return sub
    except Exception:  # noqa: BLE001
        return None


def _fetch_single(ticker: str, start: str) -> pd.DataFrame | None:
    """Per-ticker fallback when batch is missing or empty for this ticker."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hist = yf.Ticker(ticker).history(start=start, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        return hist
    except Exception as exc:  # noqa: BLE001
        logger.warning("[prices] %s single fetch failed: %s", ticker, exc)
        return None


def ingest_prices(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    period_days: int = DEFAULT_PERIOD_DAYS,
    dry_run: bool = False,
) -> PriceIngestionResult:
    """Fetch daily OHLCV for portfolio + watchlist tickers and upsert into ``prices``."""
    tickers = collect_tickers(config)
    if not tickers:
        return PriceIngestionResult(0, 0, 0, [])

    start = (date.today() - timedelta(days=max(period_days, 5) + 10)).isoformat()

    batch = _batch_download(tickers, start)

    failures: list[dict[str, str]] = []
    rows_by_ticker: dict[str, list[dict[str, Any]]] = {}

    for ticker in tickers:
        hist = _slice_batch(batch, ticker)
        if hist is None or hist.empty or hist["Close"].dropna().empty:
            hist = _fetch_single(ticker, start)
        if hist is None or hist.empty:
            failures.append({"ticker": ticker, "error": "no history returned"})
            continue

        rows = _rows_from_history(ticker, hist)
        if not rows:
            failures.append({"ticker": ticker, "error": "history empty after cleaning"})
            continue
        rows_by_ticker[ticker] = rows

    succeeded = len(rows_by_ticker)
    total_rows = sum(len(r) for r in rows_by_ticker.values())

    if dry_run:
        return PriceIngestionResult(len(tickers), succeeded, total_rows, failures)

    upserted = 0
    with get_conn(db_path) as conn:
        for rows in rows_by_ticker.values():
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO prices (
                        ticker, date, open, high, low, close, volume,
                        pct_change, pct_change_5d, pct_change_30d
                    )
                    VALUES (
                        :ticker, :date, :open, :high, :low, :close, :volume,
                        :pct_change, :pct_change_5d, :pct_change_30d
                    )
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        pct_change=excluded.pct_change,
                        pct_change_5d=excluded.pct_change_5d,
                        pct_change_30d=excluded.pct_change_30d
                    """,
                    row,
                )
                upserted += 1

    return PriceIngestionResult(
        tickers_attempted=len(tickers),
        tickers_succeeded=succeeded,
        rows_upserted=upserted,
        failures=failures,
    )


__all__ = [
    "DEFAULT_PERIOD_DAYS",
    "PriceIngestionResult",
    "collect_tickers",
    "ingest_prices",
]
