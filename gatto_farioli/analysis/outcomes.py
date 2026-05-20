"""Outcome tracking — snapshot POSSIBLE_TRADE/INVESTIGATE at emission, resolve after window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_conn, query_all, query_one

_DEFAULT_RESOLUTION_WINDOW_DAYS = 7
_DEFAULT_EQUITY_HIT_THRESHOLD_PCT = 5.0
_DEFAULT_PM_HIT_THRESHOLD_PP = 10.0
_DEFAULT_RECENT_WINDOW_DAYS = 14

_INSTRUMENT_EQUITY = "equity"
_INSTRUMENT_KALSHI = "kalshi"
_INSTRUMENT_POLYMARKET = "polymarket"
_INSTRUMENT_UNKNOWN = "unknown"

_STATUS_OPEN = "open"
_STATUS_HIT = "resolved_hit"
_STATUS_MISS = "resolved_miss"
_STATUS_NEUTRAL = "resolved_neutral"
_STATUS_UNRESOLVABLE = "unresolvable"


@dataclass(frozen=True)
class OutcomeSnapshotResult:
    candidates_seen: int
    rows_created: int
    rows_skipped_existing: int
    rows_skipped_missing_price: int


@dataclass(frozen=True)
class OutcomeResolveResult:
    rows_examined: int
    rows_resolved_hit: int
    rows_resolved_miss: int
    rows_resolved_neutral: int
    rows_resolved_unresolvable: int
    rows_still_open: int


def _now_utc(now: datetime | None) -> datetime:
    if now is not None:
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _outcomes_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("outcomes") or {}


def _resolution_window_days(cfg: dict[str, Any]) -> int:
    return int(_outcomes_cfg(cfg).get("resolution_window_days", _DEFAULT_RESOLUTION_WINDOW_DAYS))


def _equity_hit_threshold(cfg: dict[str, Any]) -> float:
    pct = float(_outcomes_cfg(cfg).get("equity_hit_threshold_pct", _DEFAULT_EQUITY_HIT_THRESHOLD_PCT))
    return pct / 100.0


def _pm_hit_threshold(cfg: dict[str, Any]) -> float:
    pp = float(_outcomes_cfg(cfg).get("prediction_market_hit_threshold_pp", _DEFAULT_PM_HIT_THRESHOLD_PP))
    return pp / 100.0


def recent_window_days(cfg: dict[str, Any]) -> int:
    return int(_outcomes_cfg(cfg).get("recent_window_days", _DEFAULT_RECENT_WINDOW_DAYS))


def _resolve_instrument(
    candidate: Any, db_path: str | Path,
) -> tuple[str, str] | None:
    ticker = candidate["related_ticker"]
    market = candidate["related_market_ticker"]
    if ticker and not market:
        return _INSTRUMENT_EQUITY, str(ticker).upper()
    if market:
        row = query_one(
            "SELECT platform FROM market_universe WHERE symbol = ?",
            (market,),
            db_path=db_path,
        )
        if row and row["platform"] in (_INSTRUMENT_KALSHI, _INSTRUMENT_POLYMARKET):
            return row["platform"], market
        return _INSTRUMENT_UNKNOWN, market
    return None


def _latest_equity_close(ticker: str, db_path: str | Path) -> float | None:
    row = query_one(
        "SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
        db_path=db_path,
    )
    return float(row["close"]) if row and row["close"] is not None else None


def _latest_pm_yes_price(platform: str, ticker: str, db_path: str | Path) -> float | None:
    row = query_one(
        """
        SELECT yes_price FROM prediction_markets
        WHERE platform = ? AND ticker = ?
        ORDER BY snapshot_at DESC LIMIT 1
        """,
        (platform, ticker),
        db_path=db_path,
    )
    return float(row["yes_price"]) if row and row["yes_price"] is not None else None


def _latest_equity_close_as_of(
    ticker: str, as_of: datetime, db_path: str | Path,
) -> float | None:
    as_of_date = as_of.astimezone(timezone.utc).strftime("%Y-%m-%d")
    row = query_one(
        """
        SELECT close FROM prices
        WHERE ticker = ? AND date <= ?
        ORDER BY date DESC LIMIT 1
        """,
        (ticker, as_of_date),
        db_path=db_path,
    )
    return float(row["close"]) if row and row["close"] is not None else None


def _latest_pm_yes_price_as_of(
    platform: str, ticker: str, as_of: datetime, db_path: str | Path,
) -> float | None:
    as_of_iso = as_of.astimezone(timezone.utc).isoformat()
    row = query_one(
        """
        SELECT yes_price FROM prediction_markets
        WHERE platform = ? AND ticker = ? AND snapshot_at <= ?
        ORDER BY snapshot_at DESC LIMIT 1
        """,
        (platform, ticker, as_of_iso),
        db_path=db_path,
    )
    return float(row["yes_price"]) if row and row["yes_price"] is not None else None


def snapshot_open_opportunities(
    cfg: dict,
    db_path: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> OutcomeSnapshotResult:
    """Snapshot open POSSIBLE_TRADE/INVESTIGATE candidates into opportunity_outcomes."""
    now_dt = _now_utc(now)
    snapshot_at = now_dt.isoformat()
    window_days = _resolution_window_days(cfg)

    candidates = query_all(
        """
        SELECT candidate_key, action, score, confidence,
               related_ticker, related_market_ticker
        FROM opportunity_candidates
        WHERE action IN ('POSSIBLE_TRADE', 'INVESTIGATE') AND status = 'open'
        """,
        db_path=db_path,
    )

    rows_created = 0
    rows_skipped_existing = 0
    rows_skipped_missing_price = 0

    for cand in candidates:
        instrument = _resolve_instrument(cand, db_path)
        if instrument is None:
            continue

        kind, symbol = instrument
        entry_price: float | None = None
        notes: str | None = None

        if kind == _INSTRUMENT_EQUITY:
            entry_price = _latest_equity_close(symbol, db_path)
            if entry_price is None:
                notes = "no equity price at snapshot"
                rows_skipped_missing_price += 1
        elif kind in (_INSTRUMENT_KALSHI, _INSTRUMENT_POLYMARKET):
            entry_price = _latest_pm_yes_price(kind, symbol, db_path)
            if entry_price is None:
                notes = "no prediction-market price at snapshot"
                rows_skipped_missing_price += 1
        else:
            notes = "unknown instrument kind at snapshot"

        if dry_run:
            before = query_one(
                """
                SELECT COUNT(*) AS n FROM opportunity_outcomes
                WHERE candidate_key = ? AND date(snapshot_at) = date(?)
                """,
                (cand["candidate_key"], snapshot_at),
                db_path=db_path,
            )
            if before and before["n"]:
                rows_skipped_existing += 1
            else:
                rows_created += 1
            continue

        with get_conn(db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_outcomes (
                    candidate_key, snapshot_at, action_at_emission,
                    score_at_emission, confidence_at_emission,
                    instrument_kind, instrument_symbol, entry_price,
                    resolution_window_days, resolution_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cand["candidate_key"],
                    snapshot_at,
                    cand["action"],
                    cand["score"],
                    cand["confidence"],
                    kind,
                    symbol,
                    entry_price,
                    window_days,
                    _STATUS_OPEN,
                    notes,
                ),
            )
            if cur.rowcount:
                rows_created += 1
            else:
                rows_skipped_existing += 1

    return OutcomeSnapshotResult(
        candidates_seen=len(candidates),
        rows_created=rows_created,
        rows_skipped_existing=rows_skipped_existing,
        rows_skipped_missing_price=rows_skipped_missing_price,
    )


def _classify_equity_return(realized: float, threshold: float) -> str:
    if realized >= threshold:
        return _STATUS_HIT
    if realized <= -threshold:
        return _STATUS_MISS
    return _STATUS_NEUTRAL


def _classify_pm_return(realized: float, threshold: float) -> str:
    if realized >= threshold:
        return _STATUS_HIT
    if realized <= -threshold:
        return _STATUS_MISS
    return _STATUS_NEUTRAL


def resolve_open_outcomes(
    cfg: dict,
    db_path: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> OutcomeResolveResult:
    """Resolve open outcome rows whose resolution window has elapsed."""
    now_dt = _now_utc(now)
    now_iso = now_dt.isoformat()
    equity_thr = _equity_hit_threshold(cfg)
    pm_thr = _pm_hit_threshold(cfg)

    open_rows = query_all(
        """
        SELECT id, candidate_key, instrument_kind, instrument_symbol,
               entry_price, resolution_window_days, snapshot_at
        FROM opportunity_outcomes
        WHERE resolution_status = 'open'
          AND datetime(snapshot_at, '+' || resolution_window_days || ' days') <= ?
        """,
        (now_iso,),
        db_path=db_path,
    )

    hit = miss = neutral = unresolvable = 0

    for row in open_rows:
        kind = row["instrument_kind"] or _INSTRUMENT_UNKNOWN
        symbol = row["instrument_symbol"] or ""
        entry = row["entry_price"]
        exit_price: float | None = None
        realized: float | None = None
        status = _STATUS_UNRESOLVABLE
        notes: str | None = None

        if kind == _INSTRUMENT_UNKNOWN:
            notes = "unknown instrument kind"
        elif entry is None:
            notes = "no exit price"
        elif kind == _INSTRUMENT_EQUITY:
            exit_price = _latest_equity_close_as_of(symbol, now_dt, db_path)
            if exit_price is None:
                notes = "no exit price"
            else:
                realized = (exit_price - entry) / entry
                status = _classify_equity_return(realized, equity_thr)
        elif kind in (_INSTRUMENT_KALSHI, _INSTRUMENT_POLYMARKET):
            exit_price = _latest_pm_yes_price_as_of(kind, symbol, now_dt, db_path)
            if exit_price is None:
                notes = "no exit price"
            else:
                realized = exit_price - entry
                status = _classify_pm_return(realized, pm_thr)
        else:
            notes = "unknown instrument kind"

        if status == _STATUS_HIT:
            hit += 1
        elif status == _STATUS_MISS:
            miss += 1
        elif status == _STATUS_NEUTRAL:
            neutral += 1
        else:
            unresolvable += 1

        if dry_run:
            continue

        with get_conn(db_path) as conn:
            conn.execute(
                """
                UPDATE opportunity_outcomes SET
                    resolved_at = ?,
                    exit_price = ?,
                    realized_return = ?,
                    resolution_status = ?,
                    notes = COALESCE(?, notes)
                WHERE id = ?
                """,
                (now_iso, exit_price, realized, status, notes, row["id"]),
            )

    remaining = query_one(
        "SELECT COUNT(*) AS n FROM opportunity_outcomes WHERE resolution_status = 'open'",
        db_path=db_path,
    )
    still_open = int(remaining["n"]) if remaining else 0

    return OutcomeResolveResult(
        rows_examined=len(open_rows),
        rows_resolved_hit=hit,
        rows_resolved_miss=miss,
        rows_resolved_neutral=neutral,
        rows_resolved_unresolvable=unresolvable,
        rows_still_open=still_open,
    )


__all__ = [
    "OutcomeResolveResult",
    "OutcomeSnapshotResult",
    "recent_window_days",
    "resolve_open_outcomes",
    "snapshot_open_opportunities",
]
