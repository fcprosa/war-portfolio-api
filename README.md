# War Portfolio API + Gatto Farioli

This repository is a **two-part macro trading workspace**:

1. **War Portfolio API** — the Vercel/browser dashboard for live portfolio cards, scanner views, news panels, charts, and Claude prompt handoff.
2. **Gatto Farioli** — a local-first Python intelligence engine that ingests news, prices, macro data, prediction markets, and thesis state into SQLite before producing Claude-ready briefs.

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

Use the dashboard exactly as before. The existing web app is rooted in `index.html`, `api/`, `lib/`, and `sw.js`. For Vercel/dashboard environment variables, use the root `.env.example` as the template.

## How to use Gatto Farioli locally

Gatto Farioli runs from the `gatto_farioli/` folder on your MacBook:

```bash
cd gatto_farioli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in API keys
python run.py
python run.py --health
```

The SQLite file is written to `gatto_farioli/argos.db`. It is gitignored and stays local.

## Prediction Markets

The dashboard brief now includes Kalshi and Polymarket positions alongside Hormuz transit data from IMF PortWatch.

### Adding prediction market positions to state

Positions live in the `predictionMarkets` array inside the Vercel Blob state. Use this curl to seed your initial Kalshi position:

```bash
curl -X POST https://war-portfolio-api.vercel.app/api/state \
  -H "Content-Type: application/json" \
  -H "X-Pin: YOUR_STATE_PIN" \
  -d '{
    "predictionMarkets": [
      {
        "platform": "kalshi",
        "ticker": "KXHORMUZNORMAL-26JUN01-T60",
        "side": "NO",
        "contracts": 393.12,
        "avgCost": 0.7631,
        "thesis": "hormuz_stays_closed_short_term"
      }
    ]
  }'
```

Replace `YOUR_STATE_PIN` with the value of your `STATE_PIN` environment variable. You can add Polymarket positions to the same array using `"platform": "polymarket"` and setting `ticker` to the Polymarket condition ID.

### Updating the PortWatch manual fallback

If the MacroMicro data endpoint is unreachable, the brief falls back to a manual value stored in Blob state. Update it with:

```bash
curl -X POST https://war-portfolio-api.vercel.app/api/state \
  -H "Content-Type: application/json" \
  -H "X-Pin: YOUR_STATE_PIN" \
  -d '{
    "portwatchManual": {
      "ma7day": 42,
      "asOf": "2026-05-08",
      "note": "Manual entry — MacroMicro unreachable"
    }
  }'
```

### New environment variables

Add to your Vercel project environment variables:

```
DEFAULT_PREDICTION_MARKETS=[]
DEFAULT_PORTWATCH_MANUAL=null
```

Both are optional — the dashboard falls back to empty state if not set.

## Gatto Farioli — current status

Gatto Farioli is at **Session 1: Foundation**. Working now: SQLite schema and DB helpers, config loading from `gatto_farioli/config.yaml`, async tier-1 RSS ingestion, URL-hash dedupe, `python run.py --health` diagnostics.

Future sessions will add prices, macro, prediction markets, LLM enrichment, thesis monitoring, Telegram/email output, and Claude-ready briefs.

## Privacy

All Gatto Farioli persistent state is local. Session 1 only contacts configured RSS feeds. The War Portfolio dashboard uses Vercel Blob for portfolio state — no third-party tracking.
