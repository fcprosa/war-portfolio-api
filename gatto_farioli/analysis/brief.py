"""Daily Edge Brief v1 — deterministic, local-only.

Generates a concise position-aware markdown brief from data already in the
SQLite store. No LLM call, no fabricated numbers. Every section is built
from explicit rules so the output is reproducible and auditable.

Stored in ``briefs`` with ``type='daily_edge_v1'`` and also returned as a
string so the CLI can print it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from analysis.delta import compute_delta
from analysis.thesis import review_theses, _SIGNAL_PATTERN
from storage.db import DEFAULT_DB_PATH, get_conn

BRIEF_TYPE = "daily_edge_v1"
WAR_START = date(2026, 2, 28)


def _normalize_side(value: Any) -> str:
    """Coerce YAML side (bool/str/None) into 'YES' or 'NO'."""
    if isinstance(value, bool):
        return "NO" if value is False else "YES"
    if value is None:
        return ""
    return str(value).strip().upper()


def _humanize_signal(label: str) -> str:
    """Turn a config signal label into a readable English phrase."""
    if not label:
        return ""
    match = _SIGNAL_PATTERN.match(label)
    if match:
        op = match.group("op").lower()
        return f"{match.group('ticker').upper()} trades {op} {match.group('threshold')}"
    return label.replace("_", " ")


# ── Action-label rules ─────────────────────────────────────────────────────
def _equity_action(position: dict[str, Any], thesis_status: str | None, conviction: int | None) -> tuple[str, int, str]:
    """Pick an action label + confidence + reason for one equity position.

    Returns (label, confidence_1_to_10, reason).
    Rules are intentionally narrow and deterministic — v1 prefers HOLD over noise.
    """
    pct1 = position.get("pct_change")
    pct5 = position.get("pct_change_5d")
    unrealized_pnl = position.get("unrealized_pnl")
    avg_cost = position.get("avg_cost") or 0
    shares = position.get("shares") or 0
    cost_basis = (avg_cost * shares) if avg_cost and shares else 0
    pnl_pct = (unrealized_pnl / cost_basis * 100.0) if (unrealized_pnl is not None and cost_basis) else None

    conv = conviction or 5

    base_confidence_by_status = {
        "broken-risk": 6,
        "weakening": 5,
        "stable": 6,
        "strengthening": 7,
        None: 5,
    }
    base_conf = base_confidence_by_status.get(thesis_status, 5)

    if thesis_status == "broken-risk":
        return "EXIT", min(10, base_conf + 1), "Thesis at broken-risk (≥2 breaking signals observed)."

    if thesis_status == "weakening" and (pnl_pct is not None and pnl_pct >= 20):
        return "TRIM", base_conf, f"Thesis weakening and position up {pnl_pct:.1f}% — lock partial gain."

    if thesis_status == "weakening":
        return "WATCH", base_conf, "Thesis weakening (≥1 breaking signal). Hold size, monitor for second break."

    if pct1 is not None and pct1 <= -5:
        return "WATCH", base_conf, f"Down {pct1:.1f}% today on stable/strengthening thesis — watch for follow-through."

    if (
        pct1 is not None and pct1 <= -3
        and thesis_status == "strengthening"
        and conv >= 8
    ):
        return "ADD", min(10, base_conf + 1), f"Down {pct1:.1f}% with strengthening thesis and conviction {conv}."

    if pct5 is not None and pct5 >= 12 and thesis_status in (None, "stable"):
        return "WATCH", base_conf, f"Up {pct5:.1f}% over 5d on a stable thesis — risk of mean reversion."

    return "HOLD", base_conf, "No deterministic action trigger fired."


def _kalshi_action(snapshot: dict[str, Any] | None, cfg_position: dict[str, Any]) -> tuple[str, int, str]:
    """Pick an action label for a Kalshi position."""
    if snapshot is None:
        return (
            "WATCH",
            3,
            "Price unavailable — Kalshi public endpoint did not return this market. Cannot recommend without a live mark.",
        )
    side = _normalize_side(cfg_position.get("side"))
    avg = cfg_position.get("avg_cost")
    contracts = cfg_position.get("contracts")
    current = snapshot.get("no_price") if side == "NO" else snapshot.get("yes_price")
    if current is None or avg is None or contracts is None:
        return ("WATCH", 4, "Position is configured but market mid-mark is missing from the snapshot.")

    move = current - float(avg)
    pnl = contracts * move
    if pnl is None:
        return ("HOLD", 5, "")

    if move <= -0.10:
        return ("WATCH", 5, f"Mark moved {move:+.2f} vs avg cost — meaningful adverse drift, monitor.")
    if move >= 0.15 and current >= 0.90:
        return ("TRIM", 6, f"Mark at {current:.2f} after {move:+.2f} gain — convex upside narrowing.")
    return ("HOLD", 6, f"Mark {current:.2f}, P&L {pnl:+.2f} — within tolerance.")


# ── Section builders ───────────────────────────────────────────────────────
def _war_day(today: date | None = None) -> int:
    today = today or date.today()
    return max(1, (today - WAR_START).days + 1)


def _fmt_pct(p: float | None) -> str:
    if p is None:
        return "n/a"
    return f"{p:+.2f}%"


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.2f}"


def _section_header() -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"# GATTO FARIOLI — DAILY EDGE BRIEF\nGenerated: {now.strftime('%Y-%m-%d %H:%M %Z')}\nWar day: {_war_day()}\n"


def _section_verdict(delta: dict, thesis_reports: list[dict], config: dict) -> str:
    positions = delta["portfolio_full"]
    total_pnl = sum((p.get("unrealized_pnl") or 0) for p in positions)
    total_mv = sum((p.get("market_value") or 0) for p in positions)
    pct_pnl = (total_pnl / (total_mv - total_pnl) * 100.0) if (total_mv - total_pnl) else None

    movers = delta["portfolio_movers"]
    mover_names = ", ".join(f"{m['ticker']} {_fmt_pct(m.get('pct_change'))}" for m in movers) or "none"

    thesis_summary = []
    for t in thesis_reports:
        delta_marker = ""
        if t.get("previous_status") and t["previous_status"] != t["status"]:
            delta_marker = f" (was {t['previous_status']})"
        thesis_summary.append(f"{t['name']}: {t['status']} (conf {t['confidence']}/10){delta_marker}")

    kalshi_unavailable = any(
        m["category"] == "prediction_market" for m in delta["missing_data"]
    )
    kalshi_note = " Kalshi mark is offline — Hormuz exposure cannot be re-marked today." if kalshi_unavailable else ""

    high_news_count = sum(1 for n in delta["important_news"] if (n.get("importance") or 0) >= 6)
    news_phrase = (
        f"{high_news_count} high-importance headline(s) in last {delta['window_hours']}h."
        if high_news_count
        else "News flow muted in last 24h."
    )

    lines = [
        "## 1. Executive Verdict",
        f"Book MV {_fmt_money(total_mv)}, unrealized P&L {_fmt_money(total_pnl)} ({_fmt_pct(pct_pnl)}).",
        f"Portfolio movers (24h): {mover_names}.",
        f"Theses: {' | '.join(thesis_summary) if thesis_summary else 'none configured'}.",
        f"{news_phrase}{kalshi_note}",
    ]
    return "\n".join(lines)


def _section_what_changed(delta: dict, thesis_reports: list[dict]) -> str:
    lines = ["## 2. What Actually Changed"]
    bullets: list[str] = []

    # Most important news, deduped by leading sector
    seen_sectors: set[str] = set()
    for n in delta["important_news"]:
        sectors = (n.get("sectors") or "").split(",") if n.get("sectors") else []
        primary = sectors[0] if sectors else "macro"
        if primary in seen_sectors and len(bullets) >= 3:
            continue
        seen_sectors.add(primary)
        title = (n.get("title") or "").strip()
        src = (n.get("source") or "").strip()
        imp = n.get("importance") or 0
        bullets.append(f"- [{primary}] {title} — {src} (importance {imp:.1f})")
        if len(bullets) >= 5:
            break

    for mover in delta["portfolio_movers"][:2]:
        bullets.append(
            f"- [portfolio] {mover['ticker']} {_fmt_pct(mover.get('pct_change'))} 1d "
            f"({_fmt_pct(mover.get('pct_change_5d'))} 5d). Thesis: {mover.get('thesis') or 'n/a'}."
        )

    for t in thesis_reports:
        if t.get("previous_status") and t["previous_status"] != t["status"]:
            bullets.append(
                f"- [thesis] {t['name']}: {t['previous_status']} → {t['status']} "
                f"(conf {t.get('previous_confidence')}→{t['confidence']})."
            )

    if not bullets:
        bullets.append("- No material delta detected in the configured window. Holding.")

    return "\n".join(lines + bullets[:7])


def _section_portfolio(delta: dict, thesis_reports: list[dict]) -> str:
    by_thesis = {t["name"]: t for t in thesis_reports}
    lines = ["## 3. Portfolio Impact"]

    if not delta["portfolio_full"]:
        lines.append("(no positions configured)")
        return "\n".join(lines)

    for p in delta["portfolio_full"]:
        thesis_name = p.get("thesis")
        thesis_report = by_thesis.get(thesis_name)
        thesis_status = thesis_report["status"] if thesis_report else None
        thesis_conf = thesis_report["confidence"] if thesis_report else None

        action, confidence, reason = _equity_action(p, thesis_status, p.get("conviction"))

        current = p.get("close") if p.get("close") is not None else p.get("current_price")
        lines.append(f"\n### {p['ticker']}")
        lines.append(
            f"Position: {p.get('shares', 0):.4f} sh @ avg "
            f"{_fmt_money(p.get('avg_cost'))} | Current {_fmt_money(current)} | "
            f"MV {_fmt_money(p.get('market_value'))} | P&L {_fmt_money(p.get('unrealized_pnl'))}"
        )
        lines.append(
            f"Move: 1d {_fmt_pct(p.get('pct_change'))} | "
            f"5d {_fmt_pct(p.get('pct_change_5d'))} | "
            f"30d {_fmt_pct(p.get('pct_change_30d'))}"
        )
        thesis_label = thesis_name or "(no thesis)"
        thesis_note = f"Thesis: {thesis_label}" + (
            f" — {thesis_status} (conf {thesis_conf}/10)" if thesis_status else ""
        )
        lines.append(thesis_note)
        lines.append(f"Action: **{action}**  |  Confidence: {confidence}/10")
        if reason:
            lines.append(f"Reason: {reason}")

    return "\n".join(lines)


def _section_prediction_markets(delta: dict, config: dict) -> str:
    lines = ["## 4. Prediction Markets"]
    cfg_markets = (config.get("portfolio", {}) or {}).get("prediction_markets", []) or []
    if not cfg_markets:
        lines.append("(no prediction market positions configured)")
        return "\n".join(lines)

    snapshots = {(s["platform"], s["ticker"]): s for s in delta["prediction_markets_latest"]}

    for cfg in cfg_markets:
        platform = (cfg.get("platform") or "").lower()
        ticker = cfg.get("ticker")
        side = _normalize_side(cfg.get("side"))
        contracts = cfg.get("contracts")
        avg = cfg.get("avg_cost")
        thesis = cfg.get("thesis")
        snap = snapshots.get((platform, ticker))

        lines.append(f"\n### {platform.upper()} {ticker}")
        if snap is None:
            lines.append(
                f"Position: {contracts} {side} @ avg "
                f"{_fmt_money(avg)} | **Price: unavailable** — "
                "live snapshot missing from DB (configured ticker did not respond on Kalshi public endpoint)."
            )
            action, confidence, reason = _kalshi_action(None, cfg)
        else:
            current = snap.get("no_price") if side == "NO" else snap.get("yes_price")
            mv = (contracts * current) if (current is not None and contracts is not None) else None
            pnl = (mv - (contracts * (avg or 0))) if (mv is not None and contracts is not None and avg is not None) else None  # noqa: E501
            resolves = snap.get("resolves_at") or "unknown"
            title = snap.get("title") or "(untitled market)"
            lines.append(f"Title: {title}")
            lines.append(
                f"Position: {contracts} {side} @ avg {_fmt_money(avg)} | "
                f"Current {_fmt_money(current)} | MV {_fmt_money(mv)} | P&L {_fmt_money(pnl)}"
            )
            lines.append(f"Resolves: {resolves}")
            action, confidence, reason = _kalshi_action(snap, cfg)

        if thesis:
            lines.append(f"Thesis: {thesis}")
        lines.append(f"Action: **{action}**  |  Confidence: {confidence}/10")
        if reason:
            lines.append(f"Reason: {reason}")

    return "\n".join(lines)


def _section_watch_next(thesis_reports: list[dict], delta: dict, config: dict) -> str:
    """Up to 5 concrete watch triggers, ordered by actionability.

    Priority:
      1. Live ticker-based breaking signals that aren't yet observed (deterministic).
      2. Uncertain breaking signals where the upstream data source is missing.
      3. Kalshi reconnection prompt when the configured market has no live mark.
      4. Uncertain confirming signals (positive-direction watches).
    """
    lines = ["## 5. Watch Next"]
    bullets: list[str] = []

    # 1. ticker-based breaking-not-observed (highest signal)
    for t in thesis_reports:
        for b in t.get("breaking_not_observed", []):
            phrase = _humanize_signal(b["label"])
            bullets.append(f"- If {phrase} ({b.get('reason') or 'live read pending'}), `{t['name']}` weakens to broken-risk.")
            if len(bullets) >= 5:
                break
        if len(bullets) >= 5:
            break

    # 2. Uncertain breaking signals — name the gap honestly so user knows what to monitor manually.
    if len(bullets) < 5:
        for t in thesis_reports:
            uncertain = t.get("uncertain", [])
            breaking_uncertain = [u for u in uncertain if u["label"] in {x["label"] for x in t.get("uncertain", [])}]
            # We don't have a direct flag for breaking-vs-confirming in `uncertain`;
            # treat *all* uncertain signals as watch items and label by thesis.
            for u in uncertain[:2]:
                phrase = _humanize_signal(u["label"])
                bullets.append(f"- If {phrase} (no live feed — manual check), `{t['name']}` direction shifts.")
                if len(bullets) >= 5:
                    break
            if len(bullets) >= 5:
                break

    # 3. Kalshi reconnection prompt
    if len(bullets) < 5 and any(m["category"] == "prediction_market" for m in delta["missing_data"]):
        bullets.append(
            "- If correct Kalshi Hormuz ticker is provided in `gatto_farioli/config.yaml`, "
            "re-run `python run.py --ingest` to restore mark-to-market on the NO leg."
        )

    # 4. Confirming-not-observed (positive triggers) — only if we still have room.
    if len(bullets) < 5:
        for t in thesis_reports:
            for c in t.get("confirming_not_observed", []):
                phrase = _humanize_signal(c["label"])
                bullets.append(f"- If {phrase} ({c.get('reason') or 'live read pending'}), `{t['name']}` strengthens.")
                if len(bullets) >= 5:
                    break
            if len(bullets) >= 5:
                break

    if not bullets:
        bullets.append("- No specific signal flips queued. Keep watching news flow and 5d move on portfolio names.")

    return "\n".join(lines + bullets[:5])


def _section_claude_context(delta: dict, thesis_reports: list[dict], config: dict) -> str:
    lines = ["## 6. Claude Context Block", "```"]

    war_day = _war_day()
    lines.append(f"[regime] day={war_day} war_started=2026-02-28")

    pos_strs = []
    for p in delta["portfolio_full"]:
        current = p.get("close") if p.get("close") is not None else p.get("current_price")
        pos_strs.append(
            f"{p['ticker']}={p.get('shares',0):.4f}sh@{p.get('avg_cost',0):.2f}"
            f"→{(current or 0):.2f} "
            f"pnl={(p.get('unrealized_pnl') or 0):+.2f} "
            f"1d={_fmt_pct(p.get('pct_change'))} "
            f"thesis={p.get('thesis')}"
        )
    if pos_strs:
        lines.append("[positions] " + " | ".join(pos_strs))

    for t in thesis_reports:
        delta_marker = ""
        if t.get("previous_status") and t["previous_status"] != t["status"]:
            delta_marker = f" prev={t['previous_status']}"
        uncertain_count = len(t.get("uncertain", []))
        confirming_total = len(t.get("confirming_observed", [])) + len(t.get("confirming_not_observed", []))
        breaking_total = len(t.get("breaking_observed", [])) + len(t.get("breaking_not_observed", []))
        lines.append(
            f"[thesis] {t['name']}: status={t['status']} conf={t['confidence']}/10"
            f" observed_conf={len(t.get('confirming_observed', []))}/{confirming_total}"
            f" observed_break={len(t.get('breaking_observed', []))}/{breaking_total}"
            f" uncertain={uncertain_count}{delta_marker}"
        )

    cfg_markets = (config.get("portfolio", {}) or {}).get("prediction_markets", []) or []
    snapshots = {(s["platform"], s["ticker"]): s for s in delta["prediction_markets_latest"]}
    for cfg in cfg_markets:
        platform = (cfg.get("platform") or "").lower()
        ticker = cfg.get("ticker")
        side = _normalize_side(cfg.get("side"))
        snap = snapshots.get((platform, ticker))
        if snap is None:
            lines.append(
                f"[predmkt] {platform}:{ticker} side={side} contracts={cfg.get('contracts')} "
                f"avg={cfg.get('avg_cost')} mark=UNAVAILABLE"
            )
        else:
            mark = snap.get("no_price") if side == "NO" else snap.get("yes_price")
            lines.append(
                f"[predmkt] {platform}:{ticker} side={side} contracts={cfg.get('contracts')} "
                f"avg={cfg.get('avg_cost')} mark={mark}"
            )

    for n in delta["important_news"][:5]:
        title = (n.get("title") or "").replace("\n", " ").strip()[:140]
        lines.append(
            f"[news] imp={n.get('importance', 0):.1f} sec={n.get('sectors') or '-'} src={n.get('source')} :: {title}"
        )

    if delta["portfolio_movers"]:
        movers = " ".join(
            f"{m['ticker']}={_fmt_pct(m.get('pct_change'))}" for m in delta["portfolio_movers"]
        )
        lines.append(f"[port_movers] {movers}")

    if delta["watchlist_movers"]:
        wl = " ".join(
            f"{m['ticker']}={_fmt_pct(m.get('pct_change'))}" for m in delta["watchlist_movers"][:8]
        )
        lines.append(f"[wl_movers] {wl}")

    for m in delta["missing_data"][:8]:
        lines.append(f"[missing] {m['category']}: {m['detail']}")

    lines.append(
        "[open_questions] "
        "(1) Confirm correct Kalshi Hormuz ticker. "
        "(2) Does CF current price still respect the trim ladder? "
        "(3) Any tier-1 ceasefire signal in last 48h that should re-tag fertilizer thesis?"
    )

    lines.append("```")
    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────
def generate_daily_brief(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    hours_back: int = 24,
    dry_run: bool = False,
) -> str:
    """Build, persist (unless dry_run), and return the Daily Edge Brief v1."""

    delta = compute_delta(hours_back=hours_back, config=config, db_path=db_path)
    thesis_reports = review_theses(config, db_path=db_path, dry_run=dry_run)

    sections = [
        _section_header(),
        _section_verdict(delta, thesis_reports, config),
        _section_what_changed(delta, thesis_reports),
        _section_portfolio(delta, thesis_reports),
        _section_prediction_markets(delta, config),
        _section_watch_next(thesis_reports, delta, config),
        _section_claude_context(delta, thesis_reports, config),
    ]
    brief_text = "\n\n".join(sections) + "\n"

    if not dry_run:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO briefs (type, content, generated_at) VALUES (?, ?, ?)",
                (BRIEF_TYPE, brief_text, now_iso),
            )

    return brief_text


__all__ = ["BRIEF_TYPE", "generate_daily_brief"]
