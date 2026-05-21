"""Phase I dialogue tests — offline, mocked Anthropic API."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from analysis.dialogue import (
    _SYSTEM_PROMPT,
    _build_context,
    ask,
)
from storage.db import get_conn, init_db


def test_build_context_returns_required_keys(tmp_db) -> None:
    init_db(tmp_db)
    ctx = _build_context({"theses": {}}, tmp_db)
    for key in (
        "as_of",
        "positions",
        "top_opportunities",
        "active_narratives",
        "recent_news",
        "recent_outcomes",
        "source_health_warnings",
        "theses",
        "last_radar",
    ):
        assert key in ctx
    assert isinstance(ctx["positions"], list)
    assert isinstance(ctx["top_opportunities"], list)
    assert isinstance(ctx["last_radar"], str)


def test_build_context_positions_populated(tmp_db) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO positions (
                ticker, shares, avg_cost, current_price, market_value,
                unrealized_pnl, thesis, conviction, last_updated
            ) VALUES ('TST', 1.0, 100.0, 110.0, 110.0, 10.0, 'test', 5, ?)
            """,
            (now,),
        )
    ctx = _build_context({"theses": {}}, tmp_db)
    assert len(ctx["positions"]) == 1
    assert ctx["positions"][0]["ticker"] == "TST"


def test_build_context_top_opportunities_ordered_by_score(tmp_db) -> None:
    init_db(tmp_db)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(tmp_db) as conn:
        conn.executemany(
            """
            INSERT INTO opportunity_candidates (
                candidate_key, title, summary, source_type, score, confidence,
                action, signals_count, missing_data, evidence, created_at, last_seen, status
            ) VALUES (?, ?, '', 'equity', ?, 5.0, 'WATCH', 1, '[]', '{}', ?, ?, 'open')
            """,
            [
                ("equity:LOW", "Low", 30.0, now, now),
                ("equity:HIGH", "High", 90.0, now, now),
                ("equity:MID", "Mid", 60.0, now, now),
            ],
        )
    ctx = _build_context({"theses": {}}, tmp_db)
    scores = [o["score"] for o in ctx["top_opportunities"]]
    assert scores[0] == 90.0
    assert scores[-1] == 30.0


@patch("analysis.dialogue.anthropic.Anthropic")
def test_ask_returns_dialogue_result_with_mocked_api(mock_anthropic, tmp_db) -> None:
    init_db(tmp_db)
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Here is my answer")]
    mock_response.model = "claude-test"
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_anthropic.return_value.messages.create.return_value = mock_response

    config = {"theses": {}, "llm": {"model_analysis": "claude-test"}}
    result = ask("test question", config, tmp_db)
    assert result.answer == "Here is my answer"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50
    assert result.model == "claude-test"


@patch("analysis.dialogue.anthropic.Anthropic")
def test_ask_dry_run_skips_api(mock_anthropic, tmp_db) -> None:
    init_db(tmp_db)
    config = {"theses": {}}
    result = ask("test question", config, tmp_db, dry_run=True)
    assert mock_anthropic.call_count == 0
    assert mock_anthropic.return_value.messages.create.call_count == 0
    assert result.answer == "[dry-run: no API call made]"
    assert result.prompt_tokens == 0


def test_system_prompt_contains_required_sections() -> None:
    for fragment in (
        "NO_EDGE",
        "POSSIBLE_TRADE",
        "**Recommendation:**",
        "**Confidence:**",
        "**Evidence:**",
        "**Invalidation:**",
        "**Execution notes:**",
    ):
        assert fragment in _SYSTEM_PROMPT
