# Gatto Farioli — Autonomous Macro Intelligence System

Gatto Farioli is a local-first macro intelligence system for a concentrated retail trader. The system ingests news, prices, macro data, prediction markets, and portfolio/thesis state so it can eventually generate Claude-ready briefs and actionable alerts without cloud infrastructure.

This repository is currently at **Session 1: Foundation**.

## Current working value

Session 1 delivers:

- Python 3.11+ project skeleton.
- SQLite schema in `argos.db`.
- User-editable `config.yaml` for portfolio, theses, watchlist, sources, thresholds, LLM budget, and schedule.
- Async tier-1 RSS ingestion with `httpx` + `feedparser`.
- URL-hash dedupe using normalized URLs.
- Health metadata in the `runs` table.
- `run.py` orchestrator with `--dry-run` and `--health`.

LLM enrichment, prices, macro, prediction markets, alerts, Telegram/email, and dashboard output are intentionally left for later sessions.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

## Verify dedupe

Run ingestion twice:

```bash
python run.py
python run.py
python run.py --health
```

The second run should insert far fewer articles because `news.url_hash` is unique.

## Database

The local SQLite file is named `argos.db` to match the locked spec. It is gitignored and should stay on the user's MacBook.

## Privacy

All persistent state is local. Session 1 only contacts configured RSS feeds. Future sessions will contact data providers and Anthropic only when those features are enabled.
