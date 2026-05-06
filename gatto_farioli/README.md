# Gatto Farioli — Local Macro Intelligence Engine

Gatto Farioli is the local-first Python engine inside the broader `war-portfolio-api` repo. It is designed to become Daniel's persistent analyst: ingesting free data sources, maintaining local SQLite state, detecting thesis/regime changes, and producing Claude-ready briefs.

This folder is intentionally self-contained. Run all Python commands from here.

## Session 1 status

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
cd gatto_farioli
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

## Inspect the database

```bash
sqlite3 argos.db
```

```sql
SELECT source, title, published_at
FROM news
ORDER BY published_at DESC
LIMIT 20;
```

## Privacy

All persistent state is local. Session 1 only contacts configured RSS feeds. Future sessions will contact data providers and Anthropic only when those features are enabled.
