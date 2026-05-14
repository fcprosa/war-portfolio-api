"""Thesis health monitoring v1.

Reads ``theses`` from config.yaml and reviews each one against currently
available data. The only signals this version can verify automatically
are *price-vs-threshold* patterns of the form ``<ticker>_above_<N>`` or
``<ticker>_below_<N>``. Every other signal label (e.g.
``russia_sanctions_intact``, ``portwatch_7dma_below_30``) is reported as
``uncertain`` so the brief shows the gap instead of inventing a verdict.

Status is derived from the count of observed confirming vs. breaking
signals — never from absence-as-confirmation. Confidence is mapped from
status and clipped to 1..10.

Conservative by design: a fuzzy keyword match against news titles would
inflate observed-signal counts and corrupt the thesis story. The brief
prefers honest uncertainty to fake confidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_conn

# Maps thesis status -> baseline confidence (1..10).
_STATUS_BASE_CONFIDENCE = {
    "broken-risk": 2,
    "weakening": 4,
    "stable": 6,
    "strengthening": 8,
}

_SIGNAL_PATTERN = re.compile(
    r"""^
        (?P<ticker>[a-z0-9][a-z0-9\-\._]*?)   # ticker token
        _(?P<op>above|below|over|under)       # comparison
        _(?P<threshold>\d+(?:\.\d+)?)         # numeric threshold
        $
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass
class SignalResolution:
    """Whether a single signal label is observed, not-observed, or uncertain."""

    label: str
    state: str           # "observed" | "not_observed" | "uncertain"
    reason: str          # short human-readable explanation
    rule_used: str       # which rule resolved it ("price" | "uncertain")


def _ticker_close(conn, ticker: str) -> float | None:
    """Return latest close for a ticker, case-preserved as stored in prices."""
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? COLLATE NOCASE ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not row or row["close"] is None:
        return None
    try:
        return float(row["close"])
    except (TypeError, ValueError):
        return None


def resolve_signal(conn, label: str) -> SignalResolution:
    """Resolve one configured signal label against the local DB."""
    match = _SIGNAL_PATTERN.match(label or "")
    if not match:
        return SignalResolution(
            label=label,
            state="uncertain",
            reason="no rule available (label is not a <ticker>_above|below_<N> pattern)",
            rule_used="uncertain",
        )

    ticker_token = match.group("ticker")
    op = match.group("op").lower()
    threshold = float(match.group("threshold"))

    # Map common signal-label tokens to actual yfinance/Yahoo tickers.
    # Anything not in the alias map is tried verbatim and uppercased.
    aliases = {
        "ipi": "IPI",
        "cf": "CF",
        "brk": "BRK-B",
        "brkb": "BRK-B",
        "mos": "MOS",
        "ntr": "NTR",
        "urea": None,    # no equity proxy; treat as uncertain
        "potash": None,  # no equity proxy
    }
    ticker = aliases.get(ticker_token.lower(), ticker_token.upper()) if ticker_token.lower() in aliases else ticker_token.upper()
    if ticker is None:
        return SignalResolution(
            label=label,
            state="uncertain",
            reason=f"no equity proxy for commodity '{ticker_token}'",
            rule_used="uncertain",
        )

    close = _ticker_close(conn, ticker)
    if close is None:
        return SignalResolution(
            label=label,
            state="uncertain",
            reason=f"no latest price for {ticker} in DB",
            rule_used="price",
        )

    if op in ("above", "over"):
        observed = close > threshold
        verdict = f"{ticker} close {close:.2f} {'>' if observed else '≤'} {threshold:.2f}"
    else:
        observed = close < threshold
        verdict = f"{ticker} close {close:.2f} {'<' if observed else '≥'} {threshold:.2f}"

    return SignalResolution(
        label=label,
        state="observed" if observed else "not_observed",
        reason=verdict,
        rule_used="price",
    )


def _classify_status(
    confirming_observed: int,
    breaking_observed: int,
    confirming_total: int,
    breaking_total: int,
) -> str:
    """Map observed-signal counts to a discrete status label."""
    if breaking_observed >= 2:
        return "broken-risk"
    if breaking_observed >= 1:
        return "weakening"
    if confirming_observed >= 1 and confirming_observed > breaking_observed:
        return "strengthening"
    return "stable"


def _confidence_for(status: str) -> int:
    """Baseline 1..10 confidence per status."""
    return _STATUS_BASE_CONFIDENCE.get(status, 5)


def review_theses(
    config: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Review every configured thesis. Return a list of structured reports."""

    theses_cfg = config.get("theses", {}) or {}
    now_iso = datetime.now(timezone.utc).isoformat()

    reports: list[dict[str, Any]] = []

    with get_conn(db_path) as conn:
        for name, body in theses_cfg.items():
            description = (body or {}).get("description", "").strip()
            confirming_labels = (body or {}).get("confirming_signals", []) or []
            breaking_labels = (body or {}).get("breaking_signals", []) or []

            confirming_resolutions = [resolve_signal(conn, lbl) for lbl in confirming_labels]
            breaking_resolutions = [resolve_signal(conn, lbl) for lbl in breaking_labels]

            confirming_observed = [r for r in confirming_resolutions if r.state == "observed"]
            breaking_observed = [r for r in breaking_resolutions if r.state == "observed"]
            uncertain_confirming = [r for r in confirming_resolutions if r.state == "uncertain"]
            uncertain_breaking = [r for r in breaking_resolutions if r.state == "uncertain"]

            status = _classify_status(
                confirming_observed=len(confirming_observed),
                breaking_observed=len(breaking_observed),
                confirming_total=len(confirming_labels),
                breaking_total=len(breaking_labels),
            )
            confidence = _confidence_for(status)

            previous = conn.execute(
                "SELECT status, confidence FROM theses WHERE name = ?",
                (name,),
            ).fetchone()
            previous_status = previous["status"] if previous else None
            previous_confidence = previous["confidence"] if previous else None

            report = {
                "name": name,
                "description": description,
                "status": status,
                "confidence": confidence,
                "previous_status": previous_status,
                "previous_confidence": previous_confidence,
                "confirming_observed": [r.__dict__ for r in confirming_observed],
                "confirming_not_observed": [r.__dict__ for r in confirming_resolutions if r.state == "not_observed"],
                "breaking_observed": [r.__dict__ for r in breaking_observed],
                "breaking_not_observed": [r.__dict__ for r in breaking_resolutions if r.state == "not_observed"],
                "uncertain": [r.__dict__ for r in (uncertain_confirming + uncertain_breaking)],
                "uncertain_ratio": _ratio(
                    len(uncertain_confirming) + len(uncertain_breaking),
                    len(confirming_labels) + len(breaking_labels),
                ),
            }
            reports.append(report)

            if dry_run:
                continue

            confirming_json = json.dumps(
                [r.label for r in confirming_observed],
                ensure_ascii=False,
            )
            breaking_json = json.dumps(
                [r.label for r in breaking_observed],
                ensure_ascii=False,
            )

            if previous is None:
                conn.execute(
                    """
                    INSERT INTO theses (
                        name, description, status, confidence,
                        confirming_signals, breaking_signals, last_reviewed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, description, status, confidence, confirming_json, breaking_json, now_iso),
                )
            else:
                conn.execute(
                    """
                    UPDATE theses
                       SET description = ?,
                           status = ?,
                           confidence = ?,
                           confirming_signals = ?,
                           breaking_signals = ?,
                           last_reviewed = ?
                     WHERE name = ?
                    """,
                    (description, status, confidence, confirming_json, breaking_json, now_iso, name),
                )

    return reports


def _ratio(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 2)


__all__ = ["SignalResolution", "resolve_signal", "review_theses"]
