codex/understand-codebase-and-tool-functionality-thwxat
# War Portfolio API + Gatto Farioli

This repository is now a **two-part macro trading workspace**:

1. **War Portfolio API** — the existing Vercel/browser dashboard for live portfolio cards, scanner views, news panels, charts, and Claude prompt handoff.
2. **Gatto Farioli** — the new local-first Python intelligence engine that will ingest news, prices, macro data, prediction markets, and thesis state into SQLite before producing Claude-ready briefs.

The two systems intentionally live in the same repo because Gatto Farioli is meant to **complement and eventually strengthen** the original War Portfolio dashboard, not replace it immediately.

## Repository map

```text
war-portfolio-api/
├── api/                 # War Portfolio Vercel serverless API routes
├── lib/                 # War Portfolio shared JavaScript data logic
├── index.html           # War Portfolio browser dashboard
├── sw.js                # War Portfolio service worker
├── package.json         # War Portfolio Node/Vercel metadata
│
└── gatto_farioli/       # Local Python intelligence engine
    ├── analysis/        # Future delta/thesis/alert/brief logic
    ├── ingestion/       # RSS now; prices/macro/markets later
    ├── output/          # Future Telegram/email/dashboard outputs
    ├── storage/         # SQLite schema and DB helpers
    ├── config.yaml      # Gatto Farioli portfolio, theses, sources, schedule
    ├── requirements.txt # Gatto Farioli Python dependencies
    └── run.py           # Gatto Farioli CLI orchestrator
```

## How to use the existing War Portfolio dashboard

Use the dashboard exactly as before. The existing web app is still rooted in:

- `index.html`
- `api/`
- `lib/`
- `sw.js`

For Vercel/dashboard environment variables, use the root `.env.example` as the template.

## How to use Gatto Farioli locally

Gatto Farioli runs from the `gatto_farioli/` folder on your MacBook:

```bash
cd gatto_farioli
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
main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
codex/understand-codebase-and-tool-functionality-thwxat
python run.py --health
```

Session 1 currently ingests tier-1 RSS feeds into a local SQLite file at `gatto_farioli/argos.db`. That DB is gitignored and should remain local.

## Current integration status

Gatto Farioli is currently **Session 1: Foundation**. It is not yet wired into the browser dashboard and does not yet generate alerts or final Claude briefs.

Working now:

- SQLite schema and DB helpers.
- Config loading from `gatto_farioli/config.yaml`.
- Async tier-1 RSS ingestion.
- URL-hash dedupe for news articles.
- `python run.py --health` diagnostics.

Future sessions will add prices, macro, prediction markets, LLM enrichment, thesis monitoring, Telegram/email output, and the main Claude-ready brief.

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
main
