"""Opportunity scoring v2 — deterministic, strict, persisted.

Scores trade *candidates* from local DB state only (no network in this module):
  - active/emerging narrative_clusters
  - market_universe (Kalshi discovery)
  - latest prices + prediction_market snapshots
  - recent high-importance news
  - source_health degradation

POSSIBLE_TRADE is intentionally hard to earn. Narrative-only, headline-only,
and politics-volume-only setups are capped at WATCH / INVESTIGATE / NO_EDGE.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_conn, query_all, query_one

logger = logging.getLogger(__name__)

# ── Action gates (Phase D invariants) ─────────────────────────────────────
POSSIBLE_TRADE_MIN_SCORE = 75.0
POSSIBLE_TRADE_MIN_CONFIDENCE = 7.0
POSSIBLE_TRADE_MIN_SIGNALS = 3

ACTION_NO_EDGE = "NO_EDGE"
ACTION_WATCH = "WATCH"
ACTION_INVESTIGATE = "INVESTIGATE"
ACTION_AVOID = "AVOID"
ACTION_POSSIBLE_TRADE = "POSSIBLE_TRADE"

ALLOWED_ACTIONS = frozenset({
    ACTION_NO_EDGE, ACTION_WATCH, ACTION_INVESTIGATE, ACTION_AVOID, ACTION_POSSIBLE_TRADE,
})

# Signal *types* counted toward signals_count and diversity checks.
SIGNAL_NARRATIVE = "narrative"
SIGNAL_NEWS = "news"
SIGNAL_PRICE = "price"
SIGNAL_PREDICTION_MARKET = "prediction_market"
SIGNAL_MARKET_UNIVERSE = "market_universe"
SIGNAL_SOURCE_HEALTH = "source_health"
SIGNAL_MACRO = "macro"

SOURCE_EQUITY = "equity"
SOURCE_PREDICTION_MARKET = "prediction_market"
SOURCE_MACRO = "macro"
SOURCE_NARRATIVE = "narrative"
SOURCE_MIXED = "mixed"

POLITICS_VOLUME_FLOOR = 5000.0  # high-volume politics-only Kalshi → cap action
NEWS_IMPORTANCE_FLOOR = 5.0
CHASED_MOVE_PCT = 5.0
FLAT_MOVE_PCT = 1.5

# ── Macro signal layer constants (Phase K) ─────────────────────────────────
_DEFAULT_MACRO_SIGNALS_CFG: dict[str, float] = {
    "wti_momentum_abs": 2.0,          # |DCOILWTICO change| > this (USD) → WTI momentum signal
    "inflation_breakeven_floor": 2.5,  # T5YIE value > this (%) → elevated inflation signal
    "hy_spread_elevated": 4.5,         # BAMLH0A0HYM2 value > this (%) → risk-off signal
    "yield_curve_inversion": 0.0,      # T10Y2Y value < this (%) → inverted yield curve signal
}
_MACRO_SCORE_BOOST_PER_SIGNAL = 5.0   # score added per triggered macro signal
_MACRO_SCORE_BOOST_MAX = 15.0         # hard ceiling on total macro score boost
_MACRO_CONFIDENCE_BOOST_PER_SIGNAL = 0.5   # confidence added per triggered macro signal
_MACRO_CONFIDENCE_BOOST_MAX = 1.5          # hard ceiling on total macro confidence boost
_MACRO_EVIDENCE_KEYS = ("DCOILWTICO", "DCOILBRENTEU", "T10Y2Y", "BAMLH0A0HYM2", "T5YIE", "DFF", "DGS10")


@dataclass
class OpportunityScoreResult:
    candidates_scored: int
    inserted: int
    updated: int
    by_action: dict[str, int] = field(default_factory=dict)


@dataclass
class _Candidate:
    candidate_key: str
    title: str
    summary: str
    source_type: str
    related_ticker: str | None
    related_market_ticker: str | None
    related_narrative_id: int | None
    score: float
    confidence: float
    action: str
    signals_count: int
    missing_data: list[str]
    evidence: dict[str, Any]
    signal_types: set[str] = field(default_factory=set)
    narrative_only: bool = False
    headline_only: bool = False
    politics_only: bool = False
    has_tradable_instrument: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sectors(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return {str(s).strip() for s in parsed if str(s).strip()}
        except json.JSONDecodeError:
            pass
        return {s.strip() for s in value.split(",") if s.strip()}
    if isinstance(value, list):
        return {str(s).strip() for s in value if str(s).strip()}
    return set()


def _parse_tickers_json(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return {str(t).upper() for t in parsed}
        except json.JSONDecodeError:
            pass
    return set()


def _get_macro_cfg(config: dict[str, Any]) -> dict[str, float]:
    """Merge user-supplied macro_signals config with built-in defaults.

    All keys are optional in config; missing keys fall back to _DEFAULT_MACRO_SIGNALS_CFG.
    """
    user = config.get("macro_signals") or {}
    return {**_DEFAULT_MACRO_SIGNALS_CFG, **{k: float(v) for k, v in user.items()}}


def _macro_signals_for_ticker(
    groups: set[str],
    macro: dict[str, dict[str, Any]],
    macro_cfg: dict[str, float],
) -> list[str]:
    """Return triggered macro signal tags for an equity candidate.

    ``macro`` is a dict of indicator → {"value": float|None, "change": float|None}.
    Returns an empty list when ``macro`` is empty (FRED key not set) or when no
    threshold is crossed — scoring is unchanged in that case.
    """
    if not macro:
        return []

    triggered: list[str] = []

    is_oil = bool(groups & {"oil", "oil_tankers"})
    is_fertilizer = "fertilizer" in groups
    is_gold = "gold" in groups
    is_defense = any("defense" in g for g in groups)

    wti_thresh = macro_cfg.get("wti_momentum_abs", _DEFAULT_MACRO_SIGNALS_CFG["wti_momentum_abs"])
    inf_floor = macro_cfg.get("inflation_breakeven_floor", _DEFAULT_MACRO_SIGNALS_CFG["inflation_breakeven_floor"])
    hy_thresh = macro_cfg.get("hy_spread_elevated", _DEFAULT_MACRO_SIGNALS_CFG["hy_spread_elevated"])
    yc_thresh = macro_cfg.get("yield_curve_inversion", _DEFAULT_MACRO_SIGNALS_CFG["yield_curve_inversion"])

    # WTI momentum — relevant for oil, tankers, fertilizer
    wti_change = (macro.get("DCOILWTICO") or {}).get("change")
    if wti_change is not None and (is_oil or is_fertilizer):
        if wti_change > wti_thresh:
            triggered.append("wti_momentum_bullish")
        elif wti_change < -wti_thresh:
            triggered.append("wti_momentum_bearish")

    # Inflation breakeven — relevant for fertilizer (cost-push), gold (inflation hedge)
    t5yie_val = (macro.get("T5YIE") or {}).get("value")
    if t5yie_val is not None and t5yie_val > inf_floor and (is_fertilizer or is_gold):
        triggered.append("inflation_breakeven_elevated")

    # Yield curve inversion — macro risk-off; tailwind for gold and defense
    yc_val = (macro.get("T10Y2Y") or {}).get("value")
    if yc_val is not None and yc_val < yc_thresh:
        triggered.append("yield_curve_inverted")
        if is_gold or is_defense:
            triggered.append("risk_off_tailwind")

    # HY credit spread widening — broad risk-off; tailwind for gold and defense
    hy_val = (macro.get("BAMLH0A0HYM2") or {}).get("value")
    if hy_val is not None and hy_val > hy_thresh:
        triggered.append("hy_spread_elevated")
        if is_gold or is_defense:
            triggered.append("risk_off_tailwind")

    # Deduplicate while preserving order
    return list(dict.fromkeys(triggered))


def _macro_signals_for_category(
    category: str,
    macro: dict[str, dict[str, Any]],
    macro_cfg: dict[str, float],
) -> list[str]:
    """Return triggered macro signal tags for a Kalshi/Polymarket candidate.

    Keyed on the market's category string rather than watchlist groups.
    Returns an empty list when ``macro`` is empty.
    """
    if not macro:
        return []

    triggered: list[str] = []
    cat = (category or "").lower()

    wti_thresh = macro_cfg.get("wti_momentum_abs", _DEFAULT_MACRO_SIGNALS_CFG["wti_momentum_abs"])
    hy_thresh = macro_cfg.get("hy_spread_elevated", _DEFAULT_MACRO_SIGNALS_CFG["hy_spread_elevated"])
    yc_thresh = macro_cfg.get("yield_curve_inversion", _DEFAULT_MACRO_SIGNALS_CFG["yield_curve_inversion"])

    is_energy = cat in {"energy", "commodities"}
    is_rates = cat in {"rates", "macro", "inflation", "economics"}
    is_geo = cat in {"geopolitics"}

    # WTI momentum → energy / commodity markets
    wti_change = (macro.get("DCOILWTICO") or {}).get("change")
    if wti_change is not None and is_energy:
        if wti_change > wti_thresh:
            triggered.append("wti_momentum_bullish")
        elif wti_change < -wti_thresh:
            triggered.append("wti_momentum_bearish")

    # Yield curve → rates and macro markets
    yc_val = (macro.get("T10Y2Y") or {}).get("value")
    if yc_val is not None and yc_val < yc_thresh and is_rates:
        triggered.append("yield_curve_inverted")

    # Fed Funds rate movement → rates markets
    dff_change = (macro.get("DFF") or {}).get("change")
    if dff_change is not None and abs(dff_change) > 0.05 and is_rates:
        triggered.append("fed_funds_moving")

    # HY spread → geopolitics (risk proxy)
    hy_val = (macro.get("BAMLH0A0HYM2") or {}).get("value")
    if hy_val is not None and hy_val > hy_thresh and is_geo:
        triggered.append("hy_spread_elevated")

    return list(dict.fromkeys(triggered))


def _has_critical_missing(missing: list[str]) -> bool:
    critical_prefixes = ("no_live_price", "no_market_odds", "no_tradable_instrument")
    return any(any(m.startswith(p) for p in critical_prefixes) for m in missing)


def _source_type_from_signals(signals: set[str]) -> str:
    kinds = signals & {SIGNAL_NARRATIVE, SIGNAL_NEWS, SIGNAL_PRICE, SIGNAL_PREDICTION_MARKET, SIGNAL_MARKET_UNIVERSE}
    if len(kinds) >= 2:
        return SOURCE_MIXED
    if SIGNAL_PRICE in kinds or SIGNAL_NEWS in kinds:
        return SOURCE_EQUITY if SIGNAL_PRICE in kinds else SOURCE_NARRATIVE
    if SIGNAL_PREDICTION_MARKET in kinds or SIGNAL_MARKET_UNIVERSE in kinds:
        return SOURCE_PREDICTION_MARKET
    if SIGNAL_NARRATIVE in kinds:
        return SOURCE_NARRATIVE
    return SOURCE_MACRO


def _finalize_action(c: _Candidate) -> None:
    """Apply hard caps then POSSIBLE_TRADE gates."""
    # Hard blocks — never POSSIBLE_TRADE
    if c.headline_only or c.narrative_only or c.politics_only:
        if c.action == ACTION_POSSIBLE_TRADE:
            c.action = ACTION_INVESTIGATE
    if _has_critical_missing(c.missing_data):
        if c.action in (ACTION_POSSIBLE_TRADE, ACTION_INVESTIGATE):
            c.action = ACTION_WATCH if c.signals_count >= 2 else ACTION_NO_EDGE

    if c.signals_count < 2:
        c.action = ACTION_NO_EDGE if c.score < 25 else ACTION_WATCH

    # POSSIBLE_TRADE requires every gate
    if c.action == ACTION_POSSIBLE_TRADE:
        diverse = len(c.signal_types) >= 2
        ok = (
            c.score >= POSSIBLE_TRADE_MIN_SCORE
            and c.confidence >= POSSIBLE_TRADE_MIN_CONFIDENCE
            and c.signals_count >= POSSIBLE_TRADE_MIN_SIGNALS
            and not _has_critical_missing(c.missing_data)
            and c.has_tradable_instrument
            and diverse
            and not c.headline_only
            and not c.narrative_only
            and not c.politics_only
        )
        if not ok:
            c.action = ACTION_INVESTIGATE if c.score >= 55 and c.signals_count >= 2 else ACTION_WATCH


def _load_context(db_path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Pull everything needed for scoring from SQLite (no HTTP)."""
    cutoff_news = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    watchlist: dict[str, list[str]] = config.get("watchlist") or {}
    ticker_to_groups: dict[str, set[str]] = {}
    for group, tickers in watchlist.items():
        for t in tickers or []:
            ticker_to_groups.setdefault(str(t).upper(), set()).add(group)

    with get_conn(db_path) as conn:
        narrative_rows = conn.execute(
            """
            SELECT id, cluster_key, title, sectors, status, article_count,
                   max_importance, momentum_24h, related_tickers
            FROM narrative_clusters
            WHERE status IN ('emerging', 'active')
            """
        ).fetchall()
        narratives = [dict(r) for r in narrative_rows]

        news_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT title, importance, sectors, published_at
                FROM news
                WHERE COALESCE(importance, 0) >= ?
                  AND COALESCE(published_at, ingested_at) >= ?
                ORDER BY importance DESC
                LIMIT 40
                """,
                (NEWS_IMPORTANCE_FLOOR, cutoff_news),
            ).fetchall()
        ]

        universe = [
            dict(r)
            for r in conn.execute(
                """
                SELECT platform, symbol, title, category, yes_price, no_price,
                       volume_24h, open_interest
                FROM market_universe
                WHERE platform = 'kalshi'
                ORDER BY COALESCE(volume_24h, 0) DESC
                LIMIT 120
                """
            ).fetchall()
        ]

        prices: dict[str, dict[str, Any]] = {}
        for t in ticker_to_groups:
            row = conn.execute(
                """
                SELECT ticker, close, pct_change, pct_change_5d
                FROM prices
                WHERE ticker = ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (t,),
            ).fetchone()
            if row:
                prices[t] = dict(row)

        unhealthy = {
            r["source"]: dict(r)
            for r in conn.execute(
                "SELECT source, status, failure_count FROM source_health WHERE status != 'ok'"
            ).fetchall()
        }

        # Macro snapshot — latest value + previous observation per indicator (Phase K)
        macro_rows = conn.execute(
            """
            SELECT m.indicator,
                   m.value,
                   prev.value AS prev_value
            FROM macro m
            LEFT JOIN macro prev
              ON prev.indicator = m.indicator
             AND prev.date = (
                   SELECT date FROM macro
                   WHERE indicator = m.indicator
                     AND date < m.date
                   ORDER BY date DESC LIMIT 1
                 )
            WHERE m.date = (
                  SELECT date FROM macro AS inner
                  WHERE inner.indicator = m.indicator
                  ORDER BY date DESC LIMIT 1
                )
            """
        ).fetchall()
        macro: dict[str, dict[str, Any]] = {}
        for row in macro_rows:
            v = row["value"]
            pv = row["prev_value"]
            change: float | None = None
            if v is not None and pv is not None:
                change = float(v) - float(pv)
            macro[row["indicator"]] = {
                "value": float(v) if v is not None else None,
                "change": change,
            }

    return {
        "narratives": narratives,
        "news": news_rows,
        "universe": universe,
        "prices": prices,
        "ticker_to_groups": ticker_to_groups,
        "unhealthy": unhealthy,
        "macro": macro,
        "macro_cfg": _get_macro_cfg(config),
    }


def _sector_overlap(sectors_a: set[str], sectors_b: set[str]) -> bool:
    return bool(sectors_a & sectors_b)


def _watchlist_groups_to_sectors(groups: set[str]) -> set[str]:
    """Map watchlist group keys to news sector tags."""
    out: set[str] = set()
    for g in groups:
        gl = g.lower()
        out.add(gl)
        if gl.startswith("defense"):
            out.add("defense")
        if "oil" in gl or "tanker" in gl:
            out.add("oil")
            out.add("shipping")
        if "fertilizer" in gl:
            out.add("fertilizer")
        if "gold" in gl:
            out.add("gold")
        if "uranium" in gl:
            out.add("broad_market")
    return out


def _news_for_sectors(news_rows: list[dict], sectors: set[str]) -> list[dict]:
    out = []
    for row in news_rows:
        ns = _parse_sectors(row.get("sectors"))
        if _sector_overlap(ns, sectors):
            out.append(row)
    return out


def _narratives_for_ticker(narratives: list[dict], ticker: str, groups: set[str]) -> list[dict]:
    t = ticker.upper()
    matched = []
    for n in narratives:
        related = _parse_tickers_json(n.get("related_tickers"))
        n_sectors = _parse_sectors(n.get("sectors"))
        if t in related:
            matched.append(n)
            continue
        if groups and _sector_overlap(n_sectors, _watchlist_groups_to_sectors(groups)):
            matched.append(n)
    return matched


def _score_equity_candidate(
    ticker: str,
    groups: set[str],
    ctx: dict[str, Any],
) -> _Candidate | None:
    """Build one equity candidate from watchlist + context."""
    prices = ctx["prices"]
    narratives = ctx["narratives"]
    news_rows = ctx["news"]

    price_row = prices.get(ticker.upper())
    narr_matches = _narratives_for_ticker(narratives, ticker, groups)
    sector_set = _watchlist_groups_to_sectors(groups)
    for n in narr_matches:
        sector_set |= _parse_sectors(n.get("sectors"))
    related_news = _news_for_sectors(news_rows, sector_set) if sector_set else []

    signals: list[str] = []
    evidence: dict[str, Any] = {"ticker": ticker, "groups": sorted(groups)}
    missing: list[str] = []
    signal_types: set[str] = set()

    if narr_matches:
        signals.append(SIGNAL_NARRATIVE)
        signal_types.add(SIGNAL_NARRATIVE)
        evidence["narratives"] = [
            {"id": n["id"], "title": n.get("title"), "status": n.get("status"), "articles": n.get("article_count")}
            for n in narr_matches[:3]
        ]

    if related_news:
        signals.append(SIGNAL_NEWS)
        signal_types.add(SIGNAL_NEWS)
        evidence["news"] = [
            {"title": r.get("title"), "importance": r.get("importance")} for r in related_news[:3]
        ]

    if price_row:
        signals.append(SIGNAL_PRICE)
        signal_types.add(SIGNAL_PRICE)
        pct1 = float(price_row.get("pct_change") or 0.0)
        pct5 = float(price_row.get("pct_change_5d") or 0.0)
        evidence["price"] = {"close": price_row.get("close"), "pct_1d": pct1, "pct_5d": pct5}
        move = max(abs(pct1), abs(pct5))
        if move >= CHASED_MOVE_PCT:
            missing.append("price_already_chased")
    else:
        missing.append("no_live_price")

    if not signals:
        return None

    narrative_only = bool(narr_matches) and not related_news and not price_row
    headline_only = bool(related_news) and len(related_news) == 1 and not narr_matches and not price_row

    score = 20.0
    score += min(25.0, len(narr_matches) * 8.0)
    score += min(20.0, len(related_news) * 6.0)
    if price_row:
        score += 15.0
        move = max(abs(float(price_row.get("pct_change") or 0)), abs(float(price_row.get("pct_change_5d") or 0)))
        if move < FLAT_MOVE_PCT:
            score += 10.0  # possible underreaction
        elif move >= CHASED_MOVE_PCT:
            score -= 15.0

    confidence = min(10.0, 2.0 + len(signal_types) * 1.5 + (0.5 * len(related_news)))
    if "no_live_price" in missing:
        confidence = max(1.0, confidence - 3.0)

    # ── Macro signal layer (Phase K) ──────────────────────────────────────
    _macro_triggered = _macro_signals_for_ticker(
        groups, ctx.get("macro", {}), ctx.get("macro_cfg", {})
    )
    if _macro_triggered:
        signal_types.add(SIGNAL_MACRO)
        signals.append(SIGNAL_MACRO)
        evidence["macro"] = {
            "signals": _macro_triggered,
            "snapshot": {
                k: ctx["macro"][k]
                for k in _MACRO_EVIDENCE_KEYS
                if k in ctx.get("macro", {})
            },
        }
        _score_boost = min(
            _MACRO_SCORE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_SCORE_BOOST_PER_SIGNAL,
        )
        _conf_boost = min(
            _MACRO_CONFIDENCE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_CONFIDENCE_BOOST_PER_SIGNAL,
        )
        score = min(100.0, score + _score_boost)
        confidence = min(10.0, confidence + _conf_boost)
    # ──────────────────────────────────────────────────────────────────────

    if narrative_only:
        action = ACTION_WATCH
    elif headline_only:
        action = ACTION_WATCH
    elif "price_already_chased" in missing:
        action = ACTION_AVOID
    elif score >= 70 and len(signal_types) >= 3 and price_row and related_news and narr_matches:
        action = ACTION_POSSIBLE_TRADE
    elif score >= 50 and len(signal_types) >= 2:
        action = ACTION_INVESTIGATE
    elif score >= 30:
        action = ACTION_WATCH
    else:
        action = ACTION_NO_EDGE

    title = f"{ticker} equity setup"
    if narr_matches:
        title = narr_matches[0].get("title") or title

    c = _Candidate(
        candidate_key=f"equity:{ticker.upper()}",
        title=title[:200],
        summary=f"{'/'.join(sorted(groups)) or 'watchlist'} — {len(signals)} signal(s)",
        source_type=_source_type_from_signals(signal_types),
        related_ticker=ticker.upper(),
        related_market_ticker=None,
        related_narrative_id=int(narr_matches[0]["id"]) if narr_matches else None,
        score=round(min(100.0, max(0.0, score)), 1),
        confidence=round(confidence, 1),
        action=action,
        signals_count=len(signals),
        missing_data=missing,
        evidence=evidence,
        signal_types=signal_types,
        narrative_only=narrative_only,
        headline_only=headline_only,
        politics_only=False,
        has_tradable_instrument=price_row is not None,
    )
    _finalize_action(c)
    return c


def _score_kalshi_candidate(market: dict[str, Any], ctx: dict[str, Any]) -> _Candidate | None:
    """Build one prediction-market candidate from market_universe row."""
    symbol = market.get("symbol") or ""
    if not symbol:
        return None
    category = (market.get("category") or "other").lower()
    if category == "sports":
        return None

    narratives = ctx["narratives"]
    news_rows = ctx["news"]
    volume = float(market.get("volume_24h") or 0.0)

    yes_p = market.get("yes_price")
    no_p = market.get("no_price")
    has_odds = yes_p is not None or no_p is not None

    narr_matches = []
    for n in narratives:
        hay = (n.get("title") or "").lower()
        if category in _parse_sectors(n.get("sectors")) or category.replace("_", " ") in hay:
            narr_matches.append(n)

    sector_set = {category, "geopolitics", "prediction_market"}
    related_news = _news_for_sectors(news_rows, sector_set)

    signals: list[str] = []
    signal_types: set[str] = set()
    missing: list[str] = []
    evidence: dict[str, Any] = {
        "market": symbol,
        "category": category,
        "title": market.get("title"),
        "volume_24h": volume,
    }

    signals.append(SIGNAL_MARKET_UNIVERSE)
    signal_types.add(SIGNAL_MARKET_UNIVERSE)
    if has_odds:
        signals.append(SIGNAL_PREDICTION_MARKET)
        signal_types.add(SIGNAL_PREDICTION_MARKET)
        evidence["odds"] = {"yes": yes_p, "no": no_p}
    else:
        missing.append("no_market_odds")

    if narr_matches:
        signals.append(SIGNAL_NARRATIVE)
        signal_types.add(SIGNAL_NARRATIVE)
        evidence["narratives"] = [{"id": n["id"], "title": n.get("title")} for n in narr_matches[:2]]

    if related_news:
        signals.append(SIGNAL_NEWS)
        signal_types.add(SIGNAL_NEWS)
        evidence["news"] = [{"title": r.get("title"), "importance": r.get("importance")} for r in related_news[:2]]

    politics_only = (
        category == "politics"
        and volume >= POLITICS_VOLUME_FLOOR
        and not narr_matches
        and not related_news
    )
    narrative_only = bool(narr_matches) and not related_news and not has_odds

    score = 15.0 + min(20.0, volume / 1000.0)
    if narr_matches:
        score += 20.0
    if related_news:
        score += 15.0
    if has_odds:
        score += 10.0
    if politics_only:
        score = min(score, 40.0)

    confidence = min(10.0, 2.0 + len(signal_types) * 1.8)
    if not has_odds:
        confidence = max(1.0, confidence - 3.0)

    # ── Macro signal layer (Phase K) ──────────────────────────────────────
    _macro_triggered = _macro_signals_for_category(
        category, ctx.get("macro", {}), ctx.get("macro_cfg", {})
    )
    if _macro_triggered:
        signal_types.add(SIGNAL_MACRO)
        signals.append(SIGNAL_MACRO)
        evidence["macro"] = {
            "signals": _macro_triggered,
            "snapshot": {
                k: ctx["macro"][k]
                for k in _MACRO_EVIDENCE_KEYS
                if k in ctx.get("macro", {})
            },
        }
        _score_boost = min(
            _MACRO_SCORE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_SCORE_BOOST_PER_SIGNAL,
        )
        _conf_boost = min(
            _MACRO_CONFIDENCE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_CONFIDENCE_BOOST_PER_SIGNAL,
        )
        score = min(100.0, score + _score_boost)
        confidence = min(10.0, confidence + _conf_boost)
    # ──────────────────────────────────────────────────────────────────────

    if politics_only or narrative_only:
        action = ACTION_WATCH
    elif not has_odds:
        action = ACTION_WATCH
    elif score >= 75 and len(signal_types) >= 3 and narr_matches and related_news and has_odds:
        action = ACTION_POSSIBLE_TRADE
    elif score >= 50 and len(signal_types) >= 2:
        action = ACTION_INVESTIGATE
    elif score >= 25:
        action = ACTION_WATCH
    else:
        action = ACTION_NO_EDGE

    c = _Candidate(
        candidate_key=f"kalshi:{symbol}",
        title=(market.get("title") or symbol)[:200],
        summary=f"Kalshi {category} — vol24h={volume:.0f}",
        source_type=SOURCE_PREDICTION_MARKET if len(signal_types) == 1 else SOURCE_MIXED,
        related_ticker=None,
        related_market_ticker=symbol,
        related_narrative_id=int(narr_matches[0]["id"]) if narr_matches else None,
        score=round(min(100.0, max(0.0, score)), 1),
        confidence=round(confidence, 1),
        action=action,
        signals_count=len(signals),
        missing_data=missing,
        evidence=evidence,
        signal_types=signal_types,
        narrative_only=narrative_only,
        headline_only=False,
        politics_only=politics_only,
        has_tradable_instrument=has_odds,
    )
    _finalize_action(c)
    return c


def _build_candidates(config: dict[str, Any], db_path: str | Path) -> list[_Candidate]:
    ctx = _load_context(db_path, config)
    unhealthy = ctx["unhealthy"]
    out: list[_Candidate] = []

    # Degrade confidence globally when key feeds are unhealthy
    health_penalty = min(3.0, len(unhealthy) * 0.5)

    for ticker, groups in ctx["ticker_to_groups"].items():
        c = _score_equity_candidate(ticker, groups, ctx)
        if c:
            if unhealthy:
                c.confidence = max(1.0, c.confidence - health_penalty)
                c.missing_data.append("source_health_degraded")
                if SIGNAL_SOURCE_HEALTH not in c.signal_types:
                    c.signals_count += 1
                    c.signal_types.add(SIGNAL_SOURCE_HEALTH)
                c.evidence["source_health"] = list(unhealthy.keys())[:5]
                _finalize_action(c)
            out.append(c)

    seen_markets: set[str] = set()
    for market in ctx["universe"]:
        sym = market.get("symbol")
        if not sym or sym in seen_markets:
            continue
        seen_markets.add(sym)
        c = _score_kalshi_candidate(market, ctx)
        if c:
            if unhealthy:
                c.confidence = max(1.0, c.confidence - health_penalty)
                c.missing_data.append("source_health_degraded")
                _finalize_action(c)
            out.append(c)

    return out


def _equity_30d_band(db_path: str | Path, ticker: str) -> tuple[float | None, float | None, float | None]:
    """Return (low, high, latest_close) for ticker over the last 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    rows = query_all(
        """
        SELECT close FROM prices
        WHERE ticker = ? AND date >= ?
        ORDER BY date DESC
        """,
        (ticker.upper(), cutoff),
        db_path=db_path,
    )
    if not rows:
        return None, None, None
    closes = [float(r["close"]) for r in rows if r["close"] is not None]
    if not closes:
        return None, None, None
    return min(closes), max(closes), closes[0]


def _prediction_market_odds(
    db_path: str | Path,
    market_ticker: str,
) -> tuple[float | None, float | None]:
    """Latest yes/no prices from prediction_markets for a market ticker."""
    row = query_one(
        """
        SELECT yes_price, no_price FROM prediction_markets
        WHERE ticker = ?
        ORDER BY snapshot_at DESC
        LIMIT 1
        """,
        (market_ticker,),
        db_path=db_path,
    )
    if not row:
        return None, None
    return row["yes_price"], row["no_price"]


def _source_health_map(db_path: str | Path) -> dict[str, str]:
    rows = query_all(
        "SELECT source, status FROM source_health",
        db_path=db_path,
    )
    return {str(r["source"]): str(r["status"] or "") for r in rows}


def compute_quality_bar(c: _Candidate, *, db_path: str | Path) -> dict[str, Any]:
    """Derive PRODUCT_VISION §7 Quality Bar fields from stored SQLite state only."""
    health = _source_health_map(db_path)

    catalyst_path: str | None = None
    if c.related_narrative_id is not None:
        narr = query_one(
            """
            SELECT title, status, article_count
            FROM narrative_clusters
            WHERE id = ? AND status IN ('active', 'emerging')
            """,
            (c.related_narrative_id,),
            db_path=db_path,
        )
        if narr:
            catalyst_path = (
                f"{narr['title']} — status: {narr['status']}, "
                f"articles in 24h: {narr['article_count']}"
            )
    if catalyst_path is None:
        news_items = c.evidence.get("news") if isinstance(c.evidence, dict) else None
        if isinstance(news_items, list) and news_items:
            first = news_items[0]
            if isinstance(first, dict) and first.get("title"):
                catalyst_path = f"news catalyst: {first['title']}"

    invalidation_trigger: str | None = None
    risk_reward_summary: str | None = None
    executable_instrument: str | None = None

    if c.related_ticker and not c.related_market_ticker:
        ticker = c.related_ticker.upper()
        low, high, px = _equity_30d_band(db_path, ticker)
        if low is not None and high is not None:
            invalidation_trigger = (
                f"close outside [{low}, {high}] (30d band on {ticker})"
            )
        if low is not None and high is not None and px is not None and px > 0:
            up_pct = round((high - px) / px * 100.0, 1)
            down_pct = round((px - low) / px * 100.0, 1)
            risk_reward_summary = f"+{up_pct}% / -{down_pct}% (30d band)"
        executable_instrument = f"equity:{ticker}"
    elif c.related_market_ticker:
        sym = c.related_market_ticker
        yes_p, no_p = _prediction_market_odds(db_path, sym)
        if yes_p is not None:
            lo = round(max(0.0, yes_p - 0.10), 4)
            hi = round(min(1.0, yes_p + 0.10), 4)
            invalidation_trigger = (
                f"yes_price outside [{lo}, {hi}] (current {yes_p})"
            )
            risk_reward_summary = (
                f"+{round((1.0 - yes_p) * 100.0, 1)}% if YES resolves / "
                f"-{round(yes_p * 100.0, 1)}% if NO resolves (current yes={yes_p})"
            )
        uni = query_one(
            "SELECT platform FROM market_universe WHERE symbol = ?",
            (sym,),
            db_path=db_path,
        )
        platform = (uni["platform"] if uni else None) or "market"
        executable_instrument = f"{platform}:{sym}"

    data_health_ok = True
    news_items = c.evidence.get("news") if isinstance(c.evidence, dict) else None
    news_sources: list[str] = []
    if isinstance(news_items, list):
        for item in news_items:
            if isinstance(item, dict) and item.get("source"):
                news_sources.append(str(item["source"]))
    if news_sources:
        for src in news_sources:
            if health.get(src) == "error":
                data_health_ok = False
                break
    if data_health_ok and c.related_market_ticker:
        uni = query_one(
            "SELECT platform FROM market_universe WHERE symbol = ?",
            (c.related_market_ticker,),
            db_path=db_path,
        )
        platform = (uni["platform"] if uni else "").lower()
        if platform == "kalshi":
            key = f"kalshi:{c.related_market_ticker}"
            if health.get(key) == "error":
                data_health_ok = False
        elif platform == "polymarket":
            if health.get("polymarket:gamma:markets") == "error":
                data_health_ok = False

    missing_items: list[str] = []
    if catalyst_path is None:
        missing_items.append("catalyst_path")
    if invalidation_trigger is None:
        missing_items.append("invalidation_trigger")
    if risk_reward_summary is None:
        missing_items.append("risk_reward_summary")
    if executable_instrument is None:
        missing_items.append("executable_instrument")
    if not data_health_ok:
        missing_items.append("data_health_ok")

    passed = len(missing_items) == 0
    return {
        "catalyst_path": catalyst_path,
        "invalidation_trigger": invalidation_trigger,
        "risk_reward_summary": risk_reward_summary,
        "executable_instrument": executable_instrument,
        "data_health_ok": data_health_ok,
        "missing_items": missing_items,
        "passed": passed,
    }


def _apply_quality_bar_downgrade(c: _Candidate, quality_bar: dict[str, Any]) -> None:
    """Cap action at WATCH when the Quality Bar is not fully met."""
    if quality_bar.get("passed"):
        return
    if c.action in (ACTION_POSSIBLE_TRADE, ACTION_INVESTIGATE):
        c.action = ACTION_WATCH


def upsert_opportunity_candidates(
    candidates: list[_Candidate],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
    quality_bars: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int]:
    """Persist candidates; return (inserted, updated) counts."""
    if dry_run or not candidates:
        return (len(candidates) if dry_run else 0, 0)

    now = _now_iso()
    inserted = updated = 0
    bars = quality_bars or {}
    with get_conn(db_path) as conn:
        for c in candidates:
            existed = conn.execute(
                "SELECT 1 FROM opportunity_candidates WHERE candidate_key = ?",
                (c.candidate_key,),
            ).fetchone()
            qb = bars.get(c.candidate_key) or compute_quality_bar(c, db_path=db_path)
            params = {
                "candidate_key": c.candidate_key,
                "title": c.title,
                "summary": c.summary,
                "source_type": c.source_type,
                "related_ticker": c.related_ticker,
                "related_market_ticker": c.related_market_ticker,
                "related_narrative_id": c.related_narrative_id,
                "score": c.score,
                "confidence": c.confidence,
                "action": c.action,
                "signals_count": c.signals_count,
                "missing_data": json.dumps(c.missing_data, ensure_ascii=False),
                "evidence": json.dumps(c.evidence, ensure_ascii=False),
                "created_at": now,
                "last_seen": now,
                "status": "open",
                "catalyst_path": qb.get("catalyst_path"),
                "invalidation_trigger": qb.get("invalidation_trigger"),
                "risk_reward_summary": qb.get("risk_reward_summary"),
                "quality_bar_passed": 1 if qb.get("passed") else 0,
                "quality_bar_missing": json.dumps(qb.get("missing_items") or [], ensure_ascii=False),
            }
            conn.execute(
                """
                INSERT INTO opportunity_candidates (
                    candidate_key, title, summary, source_type,
                    related_ticker, related_market_ticker, related_narrative_id,
                    score, confidence, action, signals_count,
                    missing_data, evidence, created_at, last_seen, status,
                    catalyst_path, invalidation_trigger, risk_reward_summary,
                    quality_bar_passed, quality_bar_missing
                ) VALUES (
                    :candidate_key, :title, :summary, :source_type,
                    :related_ticker, :related_market_ticker, :related_narrative_id,
                    :score, :confidence, :action, :signals_count,
                    :missing_data, :evidence, :created_at, :last_seen, :status,
                    :catalyst_path, :invalidation_trigger, :risk_reward_summary,
                    :quality_bar_passed, :quality_bar_missing
                )
                ON CONFLICT(candidate_key) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    source_type = excluded.source_type,
                    related_ticker = excluded.related_ticker,
                    related_market_ticker = excluded.related_market_ticker,
                    related_narrative_id = excluded.related_narrative_id,
                    score = excluded.score,
                    confidence = excluded.confidence,
                    action = excluded.action,
                    signals_count = excluded.signals_count,
                    missing_data = excluded.missing_data,
                    evidence = excluded.evidence,
                    last_seen = excluded.last_seen,
                    status = excluded.status,
                    catalyst_path = excluded.catalyst_path,
                    invalidation_trigger = excluded.invalidation_trigger,
                    risk_reward_summary = excluded.risk_reward_summary,
                    quality_bar_passed = excluded.quality_bar_passed,
                    quality_bar_missing = excluded.quality_bar_missing
                """,
                params,
            )
            if existed:
                updated += 1
            else:
                inserted += 1
    return inserted, updated


def score_opportunities(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> OpportunityScoreResult:
    """Score all candidates and persist to opportunity_candidates."""
    candidates = _build_candidates(config, db_path)
    quality_bars: dict[str, dict[str, Any]] = {}
    for c in candidates:
        qb = compute_quality_bar(c, db_path=db_path)
        _apply_quality_bar_downgrade(c, qb)
        quality_bars[c.candidate_key] = qb

    by_action: dict[str, int] = {}
    for c in candidates:
        by_action[c.action] = by_action.get(c.action, 0) + 1

    inserted, updated = upsert_opportunity_candidates(
        candidates, db_path, dry_run=dry_run, quality_bars=quality_bars,
    )
    return OpportunityScoreResult(
        candidates_scored=len(candidates),
        inserted=inserted,
        updated=updated,
        by_action=by_action,
    )


def find_opportunities(
    config: dict[str, Any] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    rescore: bool = False,
) -> dict[str, Any]:
    """Return structured opportunity view. Optionally rescore first."""
    if rescore and config is not None:
        score_opportunities(config, db_path=db_path)

    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT candidate_key, title, summary, source_type, related_ticker,
                   related_market_ticker, score, confidence, action, signals_count,
                   missing_data, evidence
            FROM opportunity_candidates
            WHERE status = 'open'
            ORDER BY score DESC
            """
        ).fetchall()

    equity_setups = []
    kalshi_setups = []
    no_edge_notes = []
    missing_data_agg: list[str] = []

    for r in rows:
        item = {
            "title": r["title"],
            "asset_or_market": r["related_ticker"] or r["related_market_ticker"],
            "category": r["source_type"],
            "action": r["action"],
            "confidence": int(r["confidence"] or 0),
            "score": r["score"],
            "signals_count": r["signals_count"],
            "evidence": r["evidence"],
            "missing_data": r["missing_data"],
        }
        if r["action"] in (ACTION_NO_EDGE, ACTION_AVOID):
            no_edge_notes.append(item)
        elif r["related_market_ticker"]:
            kalshi_setups.append(item)
        else:
            equity_setups.append(item)
        try:
            missing_data_agg.extend(json.loads(r["missing_data"] or "[]"))
        except json.JSONDecodeError:
            pass

    return {
        "equity_setups": equity_setups,
        "kalshi_setups": kalshi_setups,
        "news_dislocations": [],
        "no_edge_notes": no_edge_notes,
        "missing_data": sorted(set(missing_data_agg)),
    }


__all__ = [
    "POSSIBLE_TRADE_MIN_SCORE",
    "POSSIBLE_TRADE_MIN_CONFIDENCE",
    "POSSIBLE_TRADE_MIN_SIGNALS",
    "SIGNAL_MACRO",
    "OpportunityScoreResult",
    "compute_quality_bar",
    "score_opportunities",
    "upsert_opportunity_candidates",
    "find_opportunities",
]
