"""Sync configured portfolio positions into the ``positions`` SQLite table.

Reads ``portfolio.positions`` from config.yaml and upserts each ticker into
``positions`` together with the latest available close from ``prices``.
``current_price``, ``market_value``, and ``unrealized_pnl`` are computed
on the fly from that close. ``thesis`` and ``conviction`` are preserved
when the config value is missing, never blanked by a null upsert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_conn


@dataclass
class PositionSyncResult:
    """Structured summary of one position-sync run."""

    positions_attempted: int
    positions_synced: int
    priced: int
    unpriced: list[str] = field(default_factory=list)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    f = _safe_float(value)
    return int(f) if f is not None else None


def sync_positions(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> PositionSyncResult:
    """Upsert ``portfolio.positions`` into the SQLite ``positions`` table.

    Latest price is looked up from ``prices`` (ordered by date DESC, LIMIT 1).
    If no price exists the ticker is still synced with NULL market data and
    its ticker is appended to ``unpriced`` so the brief can flag the gap.
    """

    positions = (config.get("portfolio", {}) or {}).get("positions", []) or []
    if not positions:
        return PositionSyncResult(0, 0, 0, [])

    now_iso = datetime.now(timezone.utc).isoformat()
    synced = 0
    priced = 0
    unpriced: list[str] = []

    with get_conn(db_path) as conn:
        for pos in positions:
            ticker = (pos.get("ticker") or "").strip()
            if not ticker:
                continue
            shares = _safe_float(pos.get("shares")) or 0.0
            avg_cost = _safe_float(pos.get("avg_cost")) or 0.0
            thesis = pos.get("thesis")
            conviction = _safe_int(pos.get("conviction"))

            row = conn.execute(
                "SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            current_price = _safe_float(row["close"]) if row else None

            if current_price is None:
                market_value = None
                unrealized_pnl = None
                unpriced.append(ticker)
            else:
                market_value = round(shares * current_price, 4)
                unrealized_pnl = round(market_value - shares * avg_cost, 4)
                priced += 1

            if dry_run:
                synced += 1
                continue

            conn.execute(
                """
                INSERT INTO positions (
                    ticker, shares, avg_cost, current_price, market_value,
                    unrealized_pnl, thesis, conviction, last_updated
                )
                VALUES (
                    :ticker, :shares, :avg_cost, :current_price, :market_value,
                    :unrealized_pnl, :thesis, :conviction, :last_updated
                )
                ON CONFLICT(ticker) DO UPDATE SET
                    shares=excluded.shares,
                    avg_cost=excluded.avg_cost,
                    current_price=COALESCE(excluded.current_price, positions.current_price),
                    market_value=COALESCE(excluded.market_value, positions.market_value),
                    unrealized_pnl=COALESCE(excluded.unrealized_pnl, positions.unrealized_pnl),
                    thesis=COALESCE(excluded.thesis, positions.thesis),
                    conviction=COALESCE(excluded.conviction, positions.conviction),
                    last_updated=excluded.last_updated
                """,
                {
                    "ticker": ticker,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "market_value": market_value,
                    "unrealized_pnl": unrealized_pnl,
                    "thesis": thesis,
                    "conviction": conviction,
                    "last_updated": now_iso,
                },
            )
            synced += 1

    return PositionSyncResult(
        positions_attempted=len(positions),
        positions_synced=synced,
        priced=priced,
        unpriced=unpriced,
    )


__all__ = ["PositionSyncResult", "sync_positions"]
