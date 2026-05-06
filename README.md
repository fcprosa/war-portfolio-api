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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
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
