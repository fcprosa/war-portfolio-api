"""Daily Radar v1 — deterministic opportunity and narrative surface.

Consumes only existing SQLite tables (no network). Stored in ``briefs`` with
``type='edge_radar_v1'`` and recorded in ``runs`` as module ``radar``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.opportunities import (
    ACTION_AVOID,
    ACTION_POSSIBLE_TRADE,
    ACTION_WATCH,
)
from storage.db import DEFAULT_DB_PATH, get_conn, query_all, query_one
from storage.source_health import list_unhealthy

RADAR_TYPE = "edge_radar_v1"
_EMPTY = "_no data_"

# Top-opportunities action blocks (fixed order for grouping).
_ACTION_BLOCK_ORDER = (
    ACTION_POSSIBLE_TRADE,
    ACTION_WATCH,
    ACTION_AVOID,
)

# Ingestion modules whose staleness drives the header summary.
_INGESTION_MODULES = frozenset({
    "news",
    "news_score",
    "narratives",
    "prices",
    "kalshi",
    "kalshi_discovery",
    "opportunities",
    "state_sync",
})


def _radar_settings(cfg: dict[str, Any]) -> tuple[int, int, int]:
    """Return (top_n, staleness_hours, narrative_max) with safe defaults."""
    section = cfg.get("radar") or {}
    if not isinstance(section, dict):
        section = {}
    top_n = int(section.get("top_n") or 10)
    staleness_hours = int(section.get("staleness_hours") or 36)
    narrative_max = int(section.get("narrative_max") or 8)
    return top_n, staleness_hours, narrative_max


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    text = str(ts).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _load_runs_by_module(db_path: str | Path) -> dict[str, dict[str, Any]]:
    rows = query_all(
        "SELECT module, status, message, finished_at FROM runs ORDER BY module",
        db_path=db_path,
    )
    return {str(r["module"]): dict(r) for r in rows}


def _module_staleness(
    runs: dict[str, dict[str, Any]],
    *,
    staleness_hours: int,
    now: datetime,
) -> list[tuple[str, str, str]]:
    """Return (module, reason, finished_at_iso) for stale or errored modules."""
    cutoff = now - timedelta(hours=staleness_hours)
    flagged: list[tuple[str, str, str]] = []
    for module, row in sorted(runs.items()):
        status = (row.get("status") or "").lower()
        finished = _parse_iso(row.get("finished_at"))
        finished_s = row.get("finished_at") or "unknown"
        if status == "error":
            flagged.append((module, "status=error", finished_s))
            continue
        if finished is None:
            flagged.append((module, "finished_at unparseable", finished_s))
            continue
        if finished < cutoff:
            flagged.append((module, f"older than {staleness_hours}h", finished_s))
    return flagged


def _ingestion_summary(
    runs: dict[str, dict[str, Any]],
    *,
    staleness_hours: int,
    now: datetime,
) -> str:
    """Top-line OK vs STALE for ingestion modules present in ``runs``."""
    stale_parts: list[str] = []
    for module in sorted(_INGESTION_MODULES):
        row = runs.get(module)
        if row is None:
            continue
        flagged = _module_staleness({module: row}, staleness_hours=staleness_hours, now=now)
        if flagged:
            stale_parts.append(module)
    if stale_parts:
        return f"STALE ({', '.join(stale_parts)})"
    if not any(m in runs for m in _INGESTION_MODULES):
        return "STALE (no ingestion runs recorded)"
    return "OK"


def _parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _evidence_one_liner(evidence_raw: Any) -> str:
    if not evidence_raw:
        return "evidence=none"
    if isinstance(evidence_raw, str):
        try:
            evidence = json.loads(evidence_raw)
        except json.JSONDecodeError:
            return f"evidence={evidence_raw[:120]}"
    elif isinstance(evidence_raw, dict):
        evidence = evidence_raw
    else:
        return "evidence=unparsed"
    if not isinstance(evidence, dict) or not evidence:
        return "evidence=empty"
    parts: list[str] = []
    if evidence.get("ticker"):
        parts.append(f"ticker={evidence['ticker']}")
    if evidence.get("narratives"):
        narr = evidence["narratives"]
        if isinstance(narr, list) and narr:
            title = narr[0].get("title") if isinstance(narr[0], dict) else None
            if title:
                parts.append(f"narrative={str(title)[:60]}")
    if evidence.get("news"):
        news = evidence["news"]
        if isinstance(news, list) and news:
            title = news[0].get("title") if isinstance(news[0], dict) else None
            if title:
                parts.append(f"news={str(title)[:60]}")
    if evidence.get("price"):
        pr = evidence["price"]
        if isinstance(pr, dict) and pr.get("close") is not None:
            parts.append(f"close={pr['close']}")
    if evidence.get("market") or evidence.get("odds"):
        parts.append("market_odds=yes")
    if not parts:
        keys = sorted(evidence.keys())[:3]
        parts.append("keys=" + ",".join(keys))
    return "; ".join(parts)


def _fetch_top_opportunities(db_path: str | Path, top_n: int) -> list[dict[str, Any]]:
    rows = query_all(
        """
        SELECT candidate_key, title, score, confidence, action,
               related_ticker, related_market_ticker, signals_count, evidence
        FROM opportunity_candidates
        WHERE status = 'open'
        ORDER BY score DESC, confidence DESC, candidate_key ASC
        LIMIT ?
        """,
        (top_n,),
        db_path=db_path,
    )
    return [dict(r) for r in rows]


def _fetch_active_narratives(db_path: str | Path, narrative_max: int) -> list[dict[str, Any]]:
    rows = query_all(
        """
        SELECT title, status, article_count, momentum_24h, sectors,
               related_tickers, related_markets
        FROM narrative_clusters
        WHERE status IN ('active', 'emerging')
        ORDER BY COALESCE(momentum_24h, 0) DESC, last_seen DESC
        LIMIT ?
        """,
        (narrative_max,),
        db_path=db_path,
    )
    return [dict(r) for r in rows]


def _fetch_positions(db_path: str | Path) -> list[dict[str, Any]]:
    rows = query_all(
        "SELECT ticker, thesis, conviction FROM positions ORDER BY ticker",
        db_path=db_path,
    )
    return [dict(r) for r in rows]


def _possible_trades_by_ticker(
    opportunities: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in opportunities:
        if row.get("action") != ACTION_POSSIBLE_TRADE:
            continue
        ticker = (row.get("related_ticker") or "").upper()
        if not ticker:
            continue
        out.setdefault(ticker, []).append(row)
    return out


def _narrative_overlaps_position(
    narrative: dict[str, Any],
    *,
    ticker: str,
    thesis: str,
) -> bool:
    t = ticker.upper()
    related = {x.upper() for x in _parse_json_list(narrative.get("related_tickers"))}
    if t in related:
        return True
    thesis_l = (thesis or "").lower()
    if not thesis_l:
        return False
    for sector in _parse_json_list(narrative.get("sectors")):
        token = sector.lower().replace("_", " ")
        if len(token) >= 3 and re.search(rf"\b{re.escape(token)}\b", thesis_l):
            return True
        if token and token in thesis_l:
            return True
    return False


def _section_header(
    *,
    generated_at: datetime,
    ingestion_line: str,
) -> str:
    iso = generated_at.isoformat()
    return "\n".join([
        f"# GATTO FARIOLI — DAILY RADAR ({RADAR_TYPE})",
        f"Generated: {iso}",
        f"Ingestion staleness: {ingestion_line}",
        "",
    ])


def _section_top_opportunities(opportunities: list[dict[str, Any]]) -> str:
    lines = ["## Top opportunities"]
    if not opportunities:
        lines.append(_EMPTY)
        return "\n".join(lines)

    by_action: dict[str, list[dict[str, Any]]] = {}
    for row in opportunities:
        action = row.get("action") or "UNKNOWN"
        by_action.setdefault(str(action), []).append(row)

    blocks_written = 0
    ordered_actions = list(_ACTION_BLOCK_ORDER) + sorted(
        a for a in by_action if a not in _ACTION_BLOCK_ORDER
    )
    for action in ordered_actions:
        rows = by_action.get(action)
        if not rows:
            continue
        lines.append(f"### {action}")
        for row in rows:
            asset = row.get("related_ticker") or row.get("related_market_ticker") or "n/a"
            lines.append(
                f"- {row.get('title') or row.get('candidate_key')} | "
                f"score={row.get('score')} confidence={row.get('confidence')} "
                f"action={row.get('action')} asset={asset} "
                f"signals={row.get('signals_count')} | {_evidence_one_liner(row.get('evidence'))}"
            )
        blocks_written += 1

    if blocks_written == 0:
        lines.append(_EMPTY)
    return "\n".join(lines)


def _section_narratives(narratives: list[dict[str, Any]]) -> str:
    lines = ["## Active & emerging narratives"]
    if not narratives:
        lines.append(_EMPTY)
        return "\n".join(lines)
    for row in narratives:
        sectors = row.get("sectors") or "[]"
        tickers = row.get("related_tickers") or "[]"
        markets = row.get("related_markets") or "[]"
        lines.append(
            f"- {row.get('title')} | status={row.get('status')} "
            f"articles={row.get('article_count')} momentum_24h={row.get('momentum_24h')} "
            f"sectors={sectors} related_tickers={tickers} related_markets={markets}"
        )
    return "\n".join(lines)


def _section_position_callouts(
    positions: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    narratives: list[dict[str, Any]],
) -> str:
    lines = ["## Position-aware callouts"]
    if not positions:
        lines.append(_EMPTY)
        return "\n".join(lines)

    trades_by_ticker = _possible_trades_by_ticker(opportunities)
    any_callout = False
    for pos in positions:
        ticker = (pos.get("ticker") or "").upper()
        if not ticker:
            continue
        thesis = pos.get("thesis") or ""
        callouts: list[str] = []

        for opp in trades_by_ticker.get(ticker, []):
            callouts.append(
                f"POSSIBLE_TRADE: {opp.get('title')} "
                f"(score={opp.get('score')}, confidence={opp.get('confidence')}, "
                f"signals={opp.get('signals_count')})"
            )

        for narr in narratives:
            if _narrative_overlaps_position(narr, ticker=ticker, thesis=thesis):
                callouts.append(
                    f"narrative [{narr.get('status')}]: {narr.get('title')} "
                    f"(articles={narr.get('article_count')}, momentum_24h={narr.get('momentum_24h')})"
                )

        if callouts:
            any_callout = True
            lines.append(f"### {ticker}")
            for c in callouts:
                lines.append(f"- {c}")

    if not any_callout:
        lines.append(_EMPTY)
    return "\n".join(lines)


def _section_source_health(db_path: str | Path) -> str:
    lines = ["## Source-health warnings"]
    unhealthy = list_unhealthy(db_path)
    if not unhealthy:
        lines.append(_EMPTY)
        return "\n".join(lines)
    for row in unhealthy:
        src = row.get("source") or "unknown"
        lines.append(
            f"- {src} | status={row.get('status')} fails={row.get('failure_count')} "
            f"last_failure={row.get('last_failure') or 'n/a'} :: {(row.get('message') or '')[:120]}"
        )
    return "\n".join(lines)


def _section_missing_data(
    runs: dict[str, dict[str, Any]],
    *,
    staleness_hours: int,
    now: datetime,
) -> str:
    lines = ["## Missing-data flags"]
    flagged = _module_staleness(runs, staleness_hours=staleness_hours, now=now)
    if not flagged:
        lines.append(_EMPTY)
        return "\n".join(lines)
    for module, reason, finished_at in flagged:
        lines.append(f"- {module} | {reason} | finished_at={finished_at}")
    return "\n".join(lines)


def _build_radar_text(
    cfg: dict[str, Any],
    db_path: str | Path,
    *,
    now: datetime | None = None,
) -> str:
    top_n, staleness_hours, narrative_max = _radar_settings(cfg)
    now = now or _now_utc()
    runs = _load_runs_by_module(db_path)
    ingestion_line = _ingestion_summary(runs, staleness_hours=staleness_hours, now=now)

    opportunities = _fetch_top_opportunities(db_path, top_n)
    narratives = _fetch_active_narratives(db_path, narrative_max)
    positions = _fetch_positions(db_path)

    sections = [
        _section_header(generated_at=now, ingestion_line=ingestion_line),
        _section_top_opportunities(opportunities),
        _section_narratives(narratives),
        _section_position_callouts(positions, opportunities, narratives),
        _section_source_health(db_path),
        _section_missing_data(runs, staleness_hours=staleness_hours, now=now),
    ]
    return "\n\n".join(sections) + "\n"


def generate_daily_radar(
    cfg: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> str:
    """Build, persist (unless dry_run), and return the Daily Radar v1 markdown."""
    now = _now_utc()
    text = _build_radar_text(cfg, db_path, now=now)

    if dry_run:
        return text

    now_iso = now.isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO briefs (type, content, generated_at) VALUES (?, ?, ?)",
            (RADAR_TYPE, text, now_iso),
        )
        conn.execute(
            """
            INSERT INTO runs (module, status, message, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(module) DO UPDATE SET
                status=excluded.status,
                message=excluded.message,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at
            """,
            ("radar", "ok", "edge_radar_v1 generated", now_iso, now_iso),
        )

    return text


__all__ = ["RADAR_TYPE", "generate_daily_radar"]
