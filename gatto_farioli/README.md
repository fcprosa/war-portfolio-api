# Gatto Farioli — Local Macro Intelligence Engine

Gatto Farioli is the local-first Python engine inside the broader `war-portfolio-api` repo. It ingests free data sources, maintains local SQLite state, detects thesis changes, and produces deterministic Claude-ready briefs — **no LLM calls in the current build**.

This folder is intentionally self-contained. Run all Python commands from here.

## What works today

The current `main` branch covers **Session 1 foundation + hardening + Phase A–C + Phase D foundation + Phase E (Daily Radar)**.

| Capability | Module | Notes |
|---|---|---|
| Tier-1 RSS news ingestion + dedupe | `ingestion/news.py` | Async, URL-hash dedupe, tracking-param stripping. |
| Deterministic news scoring | `analysis/news_score.py` | 0-10 importance + multi-sector tagging. No LLM. |
| Equity price ingestion (~35d history) | `ingestion/prices.py` | yfinance; portfolio + every watchlist group. |
| Position sync (mark-to-market) | `storage/state.py` | Joins latest close → `current_price`/`market_value`/`unrealized_pnl`. |
| Kalshi public-market snapshot | `ingestion/kalshi.py` | Real `external-api.kalshi.com` endpoint; clean 404/network failure. |
| Delta detection | `analysis/delta.py` | 24h news + portfolio/watchlist movers + PM snapshot + missing-data. |
| Thesis health v1 | `analysis/thesis.py` | Resolves `<ticker>_above|below_<N>` signals; everything else honestly marked uncertain. |
| Daily Edge Brief v1 | `analysis/brief.py` | Markdown brief stored in `briefs` with `type='daily_edge_v1'`. |
| **Narrative memory** (Phase A) | `analysis/narratives.py` | Deterministic story clusters (`emerging` / `active` / `fading` / `resolved`). |
| **Kalshi market universe** (Phase B) | `ingestion/kalshi.py` | Events API discovery → `market_universe` (sports excluded by default). |
| **Source health** (Phase C) | `storage/source_health.py` | Per-feed / per-endpoint success/failure tracking. |
| **Opportunity scoring foundation** (Phase D) | `analysis/opportunities.py` | `opportunity_candidates` upsert-by-key, deterministic v2 scoring, hard `POSSIBLE_TRADE` gates (score / confidence / signals / tradable instrument / multi-source evidence). |
| **Daily Radar** (Phase E) | `analysis/radar.py` | Deterministic markdown over existing tables; stored in `briefs` with `type='edge_radar_v1'`. |
| **Polymarket ingestion + universe** (Phase F) | `ingestion/polymarket.py` | Gamma API snapshots + `market_universe` discovery (`platform='polymarket'`); sports excluded by default. |
| Verification harness (19 checks) | `scripts/verify.py` | Self-checks run against a temp DB so `argos.db` is safe. |

## What is intentionally not in this build

- **No LLM calls.** All scoring, tagging, signal resolution, and brief composition are rule-based and free.
- **No PortWatch ingestion.** PortWatch is one signal in a future delta layer; not the centerpiece.
- **No Telegram / email alerts.** Output goes to stdout + SQLite.
- **No dashboard changes.** The legacy `index.html` dashboard is untouched.

> **Local stash present, not applied.** A `git stash` entry (`wip: future-phase radar narratives kalshi`) holds future-phase work — radar surface, expanded Kalshi ingestion, and an operational user-state CLI (`user_state.yaml`). Nothing in this README depends on it. Run `git stash list` to see it; do not `git stash pop` unless you intend to land that work.

## Setup

```bash
cd gatto_farioli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # optional; only needed once LLM/FRED keys are wired
```

## Daily commands

```bash
# Run all ingestion (news + scoring + narratives + prices + kalshi + universe + state sync)
python run.py --ingest

# Print health summary
python run.py --health

# Generate and store the Daily Edge Brief (runs ingestion first)
python run.py --brief

# Generate brief from existing DB only — no network calls
python run.py --brief --no-ingest

# Generate and store the Daily Radar (runs ingestion first)
python run.py --radar

# Radar from existing DB only — no network calls
python run.py --radar --no-ingest

# Dry-run any pipeline (no DB writes)
python run.py --ingest --dry-run
```

`python run.py` with no flags is equivalent to `python run.py --ingest`.

## Verification

### Automated tests (Session 1 foundation)

From `gatto_farioli/` with the venv active:

```bash
pip install -r requirements.txt   # includes pytest + pytest-asyncio
pytest                            # unit tests, no network
```

From the **repo root**:

```bash
cd gatto_farioli && pytest
python -m gatto_farioli.run --health
```

Tests cover config validation, RSS URL normalization/dedupe, `entry_to_row` parsing, `upsert_news` duplicate handling, `init_db` schema creation, and `--health` output. RSS ingestion tests mock HTTP — no live feed calls.

### Integration harness

```bash
python scripts/verify.py
```

Runs a 19-check suite against a temporary DB:

1. config loads cleanly
2. every schema table is created (incl. `narrative_clusters`, `market_universe`, `source_health`, `opportunity_candidates`)
3. RSS URL normalization dedupes tracking params (`utm_*`, `fbclid`, `gclid`)
4. brief generation does not crash on an empty DB
5. `--health` prints expected sections
6. thesis resolver correctly classifies observed / not-observed / uncertain
7. `init_db` is idempotent for the Phase A–C tables
8. narrative clustering merges repeated Hormuz/oil headlines and keeps unrelated stories apart
9. narrative status values cover `emerging` / `active` / `fading` / `resolved`
10. Kalshi categorizer maps known events to the right buckets
11. Kalshi sports markets are excluded under the default config
12. `source_health` surfaces consistently-failing and recently-failing sources
13. Opportunity upsert updates an existing candidate instead of duplicating it
14. Opportunity action guards block weak `POSSIBLE_TRADE` candidates
15. News scoring writes `importance` and `sectors` back to the row
16. Radar generation does not crash on an empty DB
17. Radar separates `POSSIBLE_TRADE` from `WATCH` / `AVOID` in output order
18. Polymarket categorizer maps known buckets
19. Polymarket sports markets are excluded under the default config

Exit code is non-zero on the first failure. On full success the summary line reads `Verify: 19/19 passed.`

## Inspecting the database

```bash
sqlite3 argos.db
```

Useful queries:

```sql
-- Top scored headlines in the last 24h
SELECT importance, sectors, source, title
FROM news
WHERE COALESCE(published_at, ingested_at) >= datetime('now', '-24 hours')
ORDER BY importance DESC LIMIT 15;

-- Portfolio snapshot
SELECT ticker, shares, avg_cost, current_price, market_value, unrealized_pnl, thesis
FROM positions;

-- Latest brief
SELECT generated_at, substr(content, 1, 200) || '…' AS preview
FROM briefs ORDER BY generated_at DESC LIMIT 1;

-- Module health
SELECT module, status, finished_at, message FROM runs ORDER BY module;
```

## Using the brief with Claude

The brief ends with a `## 6. Claude Context Block` section. Paste **just that fenced block** into Claude to start a high-context discussion without retyping positions, thesis state, or top headlines. The rest of the brief is for your own reading.

The block is intentionally dense and bracketed-tag formatted (`[positions]`, `[thesis]`, `[predmkt]`, `[news]`, `[missing]`, `[open_questions]`) so Claude can parse it without a system prompt.

## Avoiding noise and token waste

- The brief is fully deterministic — re-running it does not cost tokens.
- Only paste the **Claude Context Block** into Claude. Section 2-5 are for your eyes.
- The brief flags missing/unavailable data explicitly. If `[missing]` shows a critical gap, fix the source before asking Claude follow-ups.
- News rows with `importance < 4` are filtered out of the brief. Only signal-rich headlines hit Claude.

## Known data quality flags

- **Kalshi**: `KXHORMUZNORMAL-26JUN01-T60` currently returns 404 on the public market endpoint. The configured position stays in `config.yaml` and the brief renders it with `Price: unavailable`. Update the ticker when the correct live identifier is known — **do not guess**.
- **EURN**: removed from `watchlist.oil_tankers` because Yahoo no longer returns data for it.

## Privacy

All persistent state is local in `gatto_farioli/argos.db` (gitignored). The engine contacts only the configured RSS feeds, yfinance/Yahoo, and the Kalshi public market endpoint. No Anthropic calls are made by the current build.
