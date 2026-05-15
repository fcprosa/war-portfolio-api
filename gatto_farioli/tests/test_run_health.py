"""CLI --health behavior tests."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from run import print_health
from storage.db import init_db


def test_print_health_shows_header_and_news_count(tmp_db, capsys) -> None:
    init_db(tmp_db)
    print_health(tmp_db)
    out = capsys.readouterr().out
    assert "Gatto Farioli health" in out
    assert "news:" in out
    assert "news: 0" in out


def test_print_health_includes_runs_when_present(tmp_db) -> None:
    init_db(tmp_db)
    from datetime import datetime, timezone
    from run import record_run

    now = datetime.now(timezone.utc)
    record_run("news", "ok", "test run", now, tmp_db)

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_health(tmp_db)
    out = buf.getvalue()
    assert "Latest runs:" in out
    assert "news:" in out
    assert "ok" in out
