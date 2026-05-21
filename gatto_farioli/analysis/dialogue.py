"""LLM dialogue layer — ask Gatto questions against local DB context only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic

from analysis.delta import compute_delta
from storage.db import query_all, query_one
from storage.source_health import list_unhealthy

_SYSTEM_PROMPT = """You are Gatto Farioli — a macro and geopolitical market intelligence pilot. Your job is to transform the provided DB context into executable, risk-aware answers. You are not a co-pilot. You are the pilot. Daniel is the final risk authority.

Personality (non-negotiable):
- Decisive, not timid.
- Probabilistic, not dogmatic.
- Concise on tactical questions; do not pad with generic market commentary.
- Brutally honest about uncertainty — say "I don't know" when the data is absent.
- Resistant to narrative hype — require evidence from the context before endorsing a thesis.
- Willing to say "no trade" often. NO_EDGE is a valid and frequent answer.
- Always accountable: state what you know, what you infer, and where you could be wrong.

Action classes (use exactly these strings in Recommendation):
NO_EDGE | WATCH | INVESTIGATE | AVOID | POSSIBLE_TRADE

Every answer must contain exactly these sections with these exact headers:

**Recommendation:** [action class — one of the five above — followed by one sentence]
**Confidence:** [1–10] — [one sentence justifying the level]
**Evidence:** [bullet list of facts cited directly from the context; no fabrication]
**Key risks:** [bullet list]
**Invalidation:** [one sentence — what single event or price level would kill this thesis]
**Execution notes:** [ticker or market, sizing note, timing — or "N/A" if NO_EDGE or AVOID]

Hard rules:
- Never invent data not present in the provided context.
- If the context lacks enough information, say so explicitly and classify as NO_EDGE.
- If asked for a ranked list, rank by score descending and show the action class next to each item.
- Do not use the words "certainly", "definitely", "obviously", or "straightforward".
- Do not add a preamble like "Great question!" or "Sure!".
"""


@dataclass(frozen=True)
class DialogueResult:
    question: str
    answer: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    context_summary: str


def _fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _build_context(config: dict, db_path: Path) -> dict:
    now = datetime.now(timezone.utc)
    as_of = now.isoformat()
    news_cutoff = (now - timedelta(hours=48)).isoformat()
    outcomes_cutoff = (now - timedelta(days=14)).isoformat()

    positions = _rows_to_dicts(query_all("SELECT * FROM positions", db_path=db_path))

    macro_snapshot = _rows_to_dicts(
        query_all(
            """
            SELECT m.indicator, m.date, m.value,
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
            ORDER BY m.indicator
            """,
            db_path=db_path,
        )
    )

    top_opportunities = _rows_to_dicts(
        query_all(
            """
            SELECT candidate_key, title, summary, action, score, confidence,
                   source_type, related_ticker, related_market_ticker,
                   catalyst_path, invalidation_trigger, risk_reward_summary,
                   quality_bar_passed, quality_bar_missing, signals_count
            FROM opportunity_candidates
            WHERE status = 'open'
            ORDER BY score DESC
            LIMIT 15
            """,
            db_path=db_path,
        )
    )

    active_narratives = _rows_to_dicts(
        query_all(
            """
            SELECT * FROM narrative_clusters
            WHERE status IN ('active', 'emerging')
            ORDER BY article_count DESC
            LIMIT 8
            """,
            db_path=db_path,
        )
    )

    recent_news = _rows_to_dicts(
        query_all(
            """
            SELECT title, summary, source, published_at,
                   importance AS importance_score, sectors
            FROM news
            WHERE published_at >= ?
              AND importance >= 5.0
            ORDER BY importance DESC
            LIMIT 20
            """,
            (news_cutoff,),
            db_path=db_path,
        )
    )

    recent_outcomes = _rows_to_dicts(
        query_all(
            """
            SELECT * FROM opportunity_outcomes
            WHERE resolved_at >= ?
              AND resolution_status LIKE 'resolved_%'
            ORDER BY resolved_at DESC
            """,
            (outcomes_cutoff,),
            db_path=db_path,
        )
    )

    source_health_warnings = list_unhealthy(db_path)

    theses = config.get("theses", {}) or {}

    radar_row = query_one(
        """
        SELECT content FROM briefs
        WHERE type = 'edge_radar_v1'
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        db_path=db_path,
    )
    last_radar = (radar_row["content"] if radar_row else "") or ""

    try:
        delta = compute_delta(config=config, db_path=db_path)
    except Exception:  # noqa: BLE001
        delta = {}

    capital_config = config.get("capital") or {}

    return {
        "as_of": as_of,
        "positions": positions,
        "macro_snapshot": macro_snapshot,
        "top_opportunities": top_opportunities,
        "active_narratives": active_narratives,
        "recent_news": recent_news,
        "recent_outcomes": recent_outcomes,
        "source_health_warnings": source_health_warnings,
        "theses": theses,
        "last_radar": last_radar,
        "delta": delta,
        "capital_config": capital_config,
    }


def _serialize_context(ctx: dict) -> str:
    lines: list[str] = [f"Context as of: {ctx['as_of']}", ""]

    lines.append("## Portfolio positions")
    if ctx["positions"]:
        for p in ctx["positions"]:
            lines.append(
                f"{p.get('ticker')} | shares={_fmt_num(p.get('shares'))} | "
                f"avg_cost={_fmt_num(p.get('avg_cost'))} | "
                f"current_price={_fmt_num(p.get('current_price'))} | "
                f"unrealized_pnl={_fmt_num(p.get('unrealized_pnl'))}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Macro snapshot")
    if ctx.get("macro_snapshot"):
        for row in ctx["macro_snapshot"]:
            value = row.get("value")
            prev_value = row.get("prev_value")
            if value is not None and prev_value is not None:
                chg = f"{float(value) - float(prev_value):.3f}"
            else:
                chg = "n/a"
            lines.append(
                f"{row.get('indicator')} | {row.get('date')} | "
                f"{_fmt_num(value)} | chg={chg}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Top opportunities (score DESC)")
    if ctx["top_opportunities"]:
        for o in ctx["top_opportunities"]:
            lines.append(
                f"[{o.get('action')}] score={_fmt_num(o.get('score'))} "
                f"conf={_fmt_num(o.get('confidence'))} | {o.get('candidate_key')} | {o.get('title')}"
            )
            if o.get("catalyst_path"):
                lines.append(f"  • catalyst: {o['catalyst_path']}")
            if o.get("invalidation_trigger"):
                lines.append(f"  • invalidate if: {o['invalidation_trigger']}")
            if o.get("risk_reward_summary"):
                lines.append(f"  • R/R: {o['risk_reward_summary']}")
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Active narratives")
    if ctx["active_narratives"]:
        for n in ctx["active_narratives"]:
            lines.append(
                f"[{n.get('status')}] {n.get('title')} | "
                f"articles={n.get('article_count')} | "
                f"momentum_24h={_fmt_num(n.get('momentum_24h'))}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Recent news (48h, importance ≥ 5)")
    if ctx["recent_news"]:
        for article in ctx["recent_news"]:
            lines.append(
                f"[{_fmt_num(article.get('importance_score'))}] "
                f"{article.get('source')} | {article.get('title')}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Recent track record")
    if ctx["recent_outcomes"]:
        for row in ctx["recent_outcomes"]:
            lines.append(
                f"{row.get('candidate_key')} | {row.get('action_at_emission')} | "
                f"{row.get('resolution_status')} | "
                f"realized={_fmt_num(row.get('realized_return'))}"
            )
    else:
        lines.append("_no resolved outcomes yet_")
    lines.append("")

    lines.append("## Source health warnings")
    if ctx["source_health_warnings"]:
        for row in ctx["source_health_warnings"]:
            lines.append(
                f"{row.get('source')} | status={row.get('status')} | "
                f"fails={row.get('failure_count')} | {row.get('message') or ''}"
            )
    else:
        lines.append("_all sources healthy_")
    lines.append("")

    lines.append("## Active theses")
    theses = ctx.get("theses") or {}
    if theses:
        for name, entry in theses.items():
            if not isinstance(entry, dict):
                continue
            desc = (entry.get("description") or "").replace("\n", " ")[:120]
            confirming = ", ".join(entry.get("confirming_signals") or [])
            breaking = ", ".join(entry.get("breaking_signals") or [])
            lines.append(f"  {name}: {desc}")
            lines.append(f"  confirming: {confirming}")
            lines.append(f"  breaking: {breaking}")
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## What changed (24h delta)")
    delta = ctx.get("delta") or {}
    portfolio_movers = delta.get("portfolio_movers") or []
    if portfolio_movers:
        for m in portfolio_movers:
            lines.append(
                f"{m.get('ticker')}: 1d={_fmt_num(m.get('pct_change'))}% "
                f"5d={_fmt_num(m.get('pct_change_5d'))}%"
            )
    watchlist_movers = (delta.get("watchlist_movers") or [])[:8]
    if watchlist_movers:
        lines.append("watchlist movers (top 8 by 1d move):")
        for m in watchlist_movers:
            lines.append(
                f"  {m.get('ticker')}: 1d={_fmt_num(m.get('pct_change'))}% "
                f"5d={_fmt_num(m.get('pct_change_5d'))}%"
            )
    delta_missing = (delta.get("missing_data") or [])[:5]
    if delta_missing:
        lines.append("data gaps:")
        for g in delta_missing:
            lines.append(f"  [{g.get('category')}] {g.get('detail', '')[:100]}")
    if not portfolio_movers and not watchlist_movers:
        lines.append("_no significant moves in the last 24h_")
    lines.append("")

    lines.append("## Capital configuration")
    capital = ctx.get("capital_config") or {}
    if capital:
        deployable = capital.get("deployable_usd", 0)
        lines.append(
            f"deployable: ${_fmt_num(deployable)} | "
            f"max_daily_risk: {_fmt_num(capital.get('max_daily_risk_pct'))}% | "
            f"max_drawdown: {_fmt_num(capital.get('max_drawdown_pct'))}% | "
            f"max_position: {_fmt_num(capital.get('max_position_size_pct'))}% | "
            f"horizon: {capital.get('time_horizon_days', 'n/a')}d"
        )
        if not deployable:
            lines.append("  NOTE: deployable_usd=0 — set capital.deployable_usd in config.yaml for sizing answers")
    else:
        lines.append("_not configured — add capital: section to config.yaml_")
    lines.append("")

    lines.append("## Last radar")
    radar = ctx.get("last_radar") or ""
    if radar:
        if len(radar) > 3000:
            lines.append(radar[:3000] + "[truncated]")
        else:
            lines.append(radar)
    else:
        lines.append("_no radar stored yet_")

    return "\n".join(lines)


def ask(
    question: str,
    config: dict,
    db_path: Path,
    *,
    dry_run: bool = False,
) -> DialogueResult:
    llm_cfg = config.get("llm", {})
    model = llm_cfg.get("model_analysis", "claude-opus-4-6")

    ctx = _build_context(config, db_path)
    serialized = _serialize_context(ctx)
    context_summary = (
        f"positions={len(ctx['positions'])} "
        f"macro={len(ctx.get('macro_snapshot', []))} "
        f"opps={len(ctx['top_opportunities'])} "
        f"narratives={len(ctx['active_narratives'])} "
        f"news={len(ctx['recent_news'])} "
        f"outcomes={len(ctx['recent_outcomes'])}"
    )

    if dry_run:
        return DialogueResult(
            question=question,
            answer="[dry-run: no API call made]",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            context_summary=context_summary,
        )

    user_message = f"{serialized}\n\n---\n\nQuestion: {question}"
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc

    answer_text = response.content[0].text
    return DialogueResult(
        question=question,
        answer=answer_text,
        model=response.model,
        prompt_tokens=response.usage.input_tokens,
        completion_tokens=response.usage.output_tokens,
        context_summary=context_summary,
    )


__all__ = [
    "DialogueResult",
    "_SYSTEM_PROMPT",
    "_build_context",
    "_serialize_context",
    "ask",
]
