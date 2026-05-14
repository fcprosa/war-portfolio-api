#!/usr/bin/env python
"""Verification harness for the Gatto Farioli local engine.

Runs a series of self-checks against a *temporary* SQLite database so the
real ``argos.db`` is never touched. Designed to be safe to run in CI or
as a smoke test after every change.

Usage:
    cd gatto_farioli
    .venv/bin/python scripts/verify.py

Exit code 0 on success, non-zero on first failure (with details printed).
"""

from __future__ import annotations

import io
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from pathlib import Path

# Make the gatto_farioli package importable when running directly.
PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


FAILED: list[str] = []
PASSED: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    marker = "PASS" if ok else "FAIL"
    line = f"[{marker}] {name}" + (f" — {detail}" if detail else "")
    print(line)
    (PASSED if ok else FAILED).append(name)


def _check(name: str, fn) -> None:
    try:
        fn()
        _record(name, True)
    except AssertionError as exc:
        _record(name, False, str(exc) or "assertion failed")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc().splitlines()[-1]
        _record(name, False, f"{exc} ({tb})")


# ── 1. Config loads ────────────────────────────────────────────────────────
def check_config_loads() -> None:
    from config import load_config

    cfg = load_config(PROJECT_DIR / "config.yaml")
    assert isinstance(cfg, dict), "config must be a dict"
    for section in ("portfolio", "theses", "watchlist", "news_sources", "alerts", "llm", "schedule"):
        assert section in cfg, f"missing required section: {section}"
    positions = cfg["portfolio"].get("positions") or []
    assert positions, "portfolio.positions must not be empty"


# ── 2. DB initializes ──────────────────────────────────────────────────────
def check_db_initializes(tmp_db: Path) -> None:
    from storage.db import init_db, query_one

    init_db(tmp_db)
    expected_tables = (
        "news", "prices", "macro", "prediction_markets",
        "portwatch", "positions", "theses", "alerts", "briefs", "runs",
    )
    for table in expected_tables:
        row = query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
            db_path=tmp_db,
        )
        assert row is not None, f"table {table!r} missing after init_db"


# ── 3. URL normalization dedupes tracking params ───────────────────────────
def check_url_normalization() -> None:
    from ingestion.news import normalize_url, url_hash

    base = "https://example.com/article?id=42"
    polluted = [
        f"{base}&utm_source=newsletter",
        f"{base}&utm_campaign=foo&fbclid=xyz",
        f"{base}&gclid=abc&UTM_MEDIUM=email",
    ]
    expected = normalize_url(base)
    for variant in polluted:
        assert normalize_url(variant) == expected, f"normalize_url didn't strip tracking params from {variant}"
        assert url_hash(variant) == url_hash(base), f"url_hash differs across variants of the same article"

    # Case insensitivity on scheme/host
    assert normalize_url("HTTPS://Example.COM/x") == normalize_url("https://example.com/x")


# ── 4. Brief on empty DB does not crash ────────────────────────────────────
def check_brief_on_empty_db(tmp_db: Path) -> None:
    from analysis.brief import generate_daily_brief
    from config import load_config
    from storage.db import init_db

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    text = generate_daily_brief(cfg, db_path=tmp_db, dry_run=True)
    assert isinstance(text, str), "brief must return a string"
    assert "GATTO FARIOLI — DAILY EDGE BRIEF" in text, "brief missing header"
    assert "Executive Verdict" in text, "brief missing executive verdict section"
    assert "Portfolio Impact" in text, "brief missing portfolio section"
    assert "Prediction Markets" in text, "brief missing prediction markets section"
    assert "Claude Context Block" in text, "brief missing claude context block"


# ── 5. --health prints expected sections ───────────────────────────────────
def check_health_runs(tmp_db: Path) -> None:
    from run import print_health
    from storage.db import init_db

    init_db(tmp_db)
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_health(tmp_db)
    output = buf.getvalue()
    assert "Gatto Farioli health" in output, "--health missing header"
    for table in ("news", "prices", "positions", "prediction_markets"):
        assert f"{table}:" in output, f"--health output missing '{table}:' line"


# ── 6. Bonus: thesis resolver handles ticker patterns ──────────────────────
def check_thesis_signal_resolver(tmp_db: Path) -> None:
    from analysis.thesis import resolve_signal
    from storage.db import init_db, get_conn

    init_db(tmp_db)
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
            ("IPI", "2026-05-14", 43.5),
        )
        observed = resolve_signal(conn, "ipi_above_35")
        not_observed = resolve_signal(conn, "ipi_above_50")
        uncertain = resolve_signal(conn, "russia_sanctions_intact")

    assert observed.state == "observed", f"expected observed, got {observed.state}"
    assert not_observed.state == "not_observed", f"expected not_observed, got {not_observed.state}"
    assert uncertain.state == "uncertain", f"expected uncertain, got {uncertain.state}"


# ── 7. News scoring writes back importance + sectors ───────────────────────
def check_news_scoring(tmp_db: Path) -> None:
    from analysis.news_score import score_news
    from config import load_config
    from storage.db import init_db, get_conn, query_one

    init_db(tmp_db)
    cfg = load_config(PROJECT_DIR / "config.yaml")
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO news (url_hash, url, source, title, summary, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "hash_oil_iran",
                "https://example.com/x",
                "Example",
                "Iran threatens to close Hormuz strait as Brent crude jumps",
                "Brent crude price reacts to Iran tensions over Hormuz.",
                "2026-05-14T12:00:00+00:00",
            ),
        )

    result = score_news(cfg, db_path=tmp_db)
    assert result.rows_scored == 1, f"expected 1 scored, got {result.rows_scored}"
    row = query_one("SELECT importance, sectors FROM news LIMIT 1", db_path=tmp_db)
    assert row["importance"] is not None and row["importance"] > 0, "importance not written"
    sectors = row["sectors"] or ""
    assert "geopolitics" in sectors or "oil" in sectors, f"expected geopolitics/oil tag, got {sectors!r}"


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"Gatto Farioli verify — project at {PROJECT_DIR}\n")

    with tempfile.TemporaryDirectory(prefix="gatto-verify-") as tmpdir:
        tmp_db = Path(tmpdir) / "verify.db"

        _check("config loads", check_config_loads)
        _check("db initializes (every table present)", lambda: check_db_initializes(tmp_db))
        _check("news url normalization dedupes tracking params", check_url_normalization)
        _check("brief generation does not crash on empty db", lambda: check_brief_on_empty_db(tmp_db))
        _check("--health prints expected sections", lambda: check_health_runs(tmp_db))
        _check("thesis resolver handles ticker/uncertain patterns", lambda: check_thesis_signal_resolver(tmp_db))
        _check("news scoring writes importance + sectors", lambda: check_news_scoring(tmp_db))

    total = len(PASSED) + len(FAILED)
    print(f"\nVerify: {len(PASSED)}/{total} passed.")
    if FAILED:
        print("Failures:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
