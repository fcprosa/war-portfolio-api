# Cursor prompt — Phase F: Polymarket ingestion (mirrors Phase B Kalshi)

Paste everything below this line into Cursor. Do not edit the guardrails.

---

## Context (read first; do not modify)

Repo: `war-portfolio-api`. Work only inside `gatto_farioli/` unless a README change is explicitly required.

Current `main` (HEAD `ee3f488`) ships Sessions 1 + hardening + Phase A–E:

- Phase A — narratives (`analysis/narratives.py`, `narrative_clusters` table)
- Phase B — Kalshi snapshot + universe discovery (`ingestion/kalshi.py`, `market_universe` table)
- Phase C — source health (`storage/source_health.py`, `source_health` table)
- Phase D foundation — strict opportunity scoring (`analysis/opportunities.py`, `opportunity_candidates` table)
- Phase E — Daily Radar (`analysis/radar.py`, `briefs.type='edge_radar_v1'`)
- Daily Edge Brief v1 (`analysis/brief.py`)
- 17-check verify harness; 32 pytests; all green.

There is **no Polymarket ingestion on `main`** — only a stub `gatto_farioli/ingestion/polymarket.py` that returns `None`. The schema is already Polymarket-ready: `prediction_markets.platform` and `market_universe.platform` both take `'polymarket'` without migration. A local `git stash` entry (`stash@{0}: On main: wip: future-phase radar narratives kalshi`) exists; **do NOT pop, apply, or read from it**.

## Goal

Ship Phase F: real Polymarket ingestion mirroring the Phase B Kalshi pattern exactly. Snapshot configured Polymarket positions into `prediction_markets`, discover the open Polymarket universe into `market_universe`, categorize into the same canonical buckets Kalshi uses, exclude sports by default, record per-endpoint source health. No new tables. No LLM. No schema changes.

## Reference implementation to mirror

Read these files in full before writing a single Polymarket line. They define the contract you must match in shape, error handling, dataclass style, and run.py wiring:

- `gatto_farioli/ingestion/kalshi.py` — full template (snapshot + discovery + categorizer + filter + source-health). Mirror every public function name pattern: `ingest_polymarket_markets`, `discover_polymarket_universe`, `categorize_polymarket_event`, `filter_polymarket_events`.
- `gatto_farioli/run.py` — see `_run_kalshi` and `_run_kalshi_discovery`. Add `_run_polymarket` and `_run_polymarket_discovery` in the same shape.
- `gatto_farioli/config.yaml` — see the `kalshi:` block. Mirror it as `polymarket:`.
- `gatto_farioli/storage/source_health.py` — use `record_success` / `record_failure` exactly as Kalshi does.
- `gatto_farioli/tests/test_*.py` — match the existing fixture style (`tmp_db`, `minimal_config`).

## In scope

1. **Replace the stub `gatto_farioli/ingestion/polymarket.py`** with a real implementation exposing:
   - `ingest_polymarket_markets(cfg, db_path, *, dry_run=False) -> PolymarketIngestionResult` — snapshot the `platform='polymarket'` entries from `cfg['portfolio']['prediction_markets']`, write rows to `prediction_markets`, record per-condition-id `source_health`.
   - `discover_polymarket_universe(cfg, db_path, *, dry_run=False) -> PolymarketDiscoveryResult` — page the public Polymarket Gamma API (`https://gamma-api.polymarket.com`) for open markets, categorize, filter by `polymarket.include_categories` / `polymarket.exclude_categories`, upsert into `market_universe` with `platform='polymarket'`. Also write one snapshot row per discovered market into `prediction_markets`.
   - `categorize_polymarket_event(event: dict) -> str` — map a Polymarket market/event into one of: `politics`, `crypto`, `sports`, `economics`, `rates`, `inflation`, `commodities`, `energy`, `geopolitics`, `weather`, `macro`, `other`. **Use the same canonical bucket set as `categorize_kalshi_event` — do not invent new buckets.**
   - `filter_polymarket_events(events, *, include_categories, exclude_categories) -> list[tuple[event, category]]` — mirror `filter_kalshi_events` semantics exactly.
   - Public dataclasses `PolymarketIngestionResult` and `PolymarketDiscoveryResult` matching the field shape of the Kalshi equivalents.

2. **`config.yaml`** — add a `polymarket:` block immediately after `kalshi:`, mirroring its structure:

   ```yaml
   polymarket:
     include_categories:
       - macro
       - rates
       - inflation
       - commodities
       - energy
       - geopolitics
       - economics
       - politics
       - weather
       - crypto
     exclude_categories:
       - sports
     min_volume_24h: 0
     min_open_interest: 0
     max_markets_per_run: 500
   ```

   Do not change the `kalshi:` block. Do not change any other config section.

3. **`run.py`** — add two new internal runners and wire them into the default ingest pipeline:
   - `_run_polymarket(config, args)` — mirrors `_run_kalshi`.
   - `_run_polymarket_discovery(config, args)` — mirrors `_run_kalshi_discovery`.
   - Both must respect `--dry-run` and never call the network when invoked from `--brief --no-ingest` or `--radar --no-ingest`.
   - `--ingest` (default) runs Polymarket snapshot and Polymarket discovery in the same place Kalshi runs.

4. **Source-health integration** — every Polymarket HTTP call must record `record_success` or `record_failure` against a stable source key (`polymarket:gamma:markets` for the discovery endpoint, `polymarket:gamma:<condition_id>` for per-position snapshots). Mirror the Kalshi key shape.

5. **Tests — new file `gatto_farioli/tests/test_polymarket.py` with ≥ 6 tests, no network:**
   1. `categorize_polymarket_event` maps a sample politics market, a crypto market, a sports market, an economics market, and an unknown-category market to the right canonical buckets (incl. `other` for unknown).
   2. `filter_polymarket_events` drops sports markets under the default config and keeps non-sports.
   3. snapshot row conforms to the `prediction_markets` schema when given a synthetic market dict (no network — pass a fake response payload).
   4. discovery row conforms to the `market_universe` schema for a synthetic event payload, with `platform='polymarket'`.
   5. failure path: when the HTTP client raises (use `monkeypatch` to make the request raise), the function returns an error result and `source_health.list_unhealthy` includes the `polymarket:gamma:markets` source.
   6. zero configured Polymarket positions → `ingest_polymarket_markets` returns a result with `snapshots=0` and writes no `prediction_markets` rows, exit cleanly.

6. **Verify harness — extend `gatto_farioli/scripts/verify.py` from 17 → 19 checks**, appended after the existing 17 in this order:
   - **18. `polymarket categorizer maps known buckets`** — like check 10 (Kalshi categorizer), with a small table of fixed inputs and expected outputs.
   - **19. `polymarket sports excluded by default config`** — like check 11 (Kalshi sports excluded), against the new `polymarket:` config block.
   - Final summary line must read exactly: `Verify: 19/19 passed.`

7. **Docs:**
   - `gatto_farioli/README.md` — add a Phase F row to the "What works today" capability table; bump verify count to 19; append checks 18 and 19; remove `No Polymarket ingestion.` from the "What is intentionally not in this build" list (the `ingestion/polymarket.py` stub line is now incorrect — replace with a brief note that the live Polymarket Gamma API is wired).
   - `README.md` (root) — append "Polymarket ingestion + universe discovery" to the "What's working on `main`" list; bump verify count to 19; remove "Polymarket ingestion" from the "Not yet built (deliberately)" line.

## Out of scope — do NOT do any of these

- No schema migrations. No new tables. No `ALTER TABLE`. `prediction_markets` and `market_universe` are reused exactly as defined in `storage/schema.py`.
- No LLM, no Anthropic API, no enrichment of Polymarket titles.
- No changes to `ingestion/kalshi.py` (do not refactor Kalshi to share code with Polymarket; copy patterns, don't extract a shared base).
- No changes to `analysis/brief.py`, `analysis/radar.py`, `analysis/opportunities.py`, `analysis/narratives.py`, `analysis/news_score.py`, `analysis/thesis.py`, `analysis/delta.py`, `analysis/alerts.py`.
- No changes to `storage/db.py`, `storage/state.py`, `storage/source_health.py` public API, `storage/schema.py`.
- No new ingestion sources beyond Polymarket (no PortWatch, Twitter, Filings, FRED).
- No dashboard / `index.html` / Vercel `api/` / `lib/` / `sw.js` changes.
- No Telegram, email, push, or webhook outputs.
- No changes to `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`.
- No additions to `requirements.txt` (`httpx` is already a dependency; that's the only HTTP client you may use).
- Do not `git stash pop`, `git stash apply`, or otherwise touch `stash@{0}`.
- Do not add Polymarket positions to `config.yaml.portfolio.prediction_markets`. The user (Daniel) will add positions later. The snapshot path must run cleanly with zero configured Polymarket positions.
- Do not change the canonical bucket set used by the categorizer — reuse the exact strings `categorize_kalshi_event` returns.

## Definition of done — all must pass before committing

```bash
cd gatto_farioli
python -m pytest -q                                              # >= 38 passed (32 existing + ≥6 new)
python scripts/verify.py                                         # last line: "Verify: 19/19 passed."
python -m gatto_farioli.run --health                             # exits 0, no tracebacks
python -m gatto_farioli.run --ingest --dry-run                   # exits 0, polymarket + polymarket_discovery runners appear in stdout
python -m gatto_farioli.run --radar --no-ingest                  # still works; polymarket markets appear in market_universe via prior ingest
python -m gatto_farioli.run --brief --no-ingest                  # still works; existing brief format unchanged
git diff --stat                                                  # only allowed paths touched (see Out of scope)
```

Also confirm `git show <commit> --stat` lists only these paths:

- `gatto_farioli/ingestion/polymarket.py`
- `gatto_farioli/run.py`
- `gatto_farioli/config.yaml`
- `gatto_farioli/scripts/verify.py`
- `gatto_farioli/tests/test_polymarket.py`
- `gatto_farioli/README.md`
- `README.md`

Nothing else.

## Guardrails for the agent

- Before writing code, read in full: `ingestion/kalshi.py`, `run.py`, `storage/source_health.py`, `scripts/verify.py`, `tests/test_opportunities.py` (for test style). Mirror their patterns.
- Use `storage.db.get_conn` / `query_one` / `query_all` for DB access. No raw `sqlite3.connect`.
- Use `httpx.Client` (sync) with explicit timeouts, exactly as Kalshi does. No `requests`, no `aiohttp` in Polymarket.
- All timestamps are `datetime.now(timezone.utc)`. No naive datetimes.
- All output is deterministic given the same Polymarket payload — no randomness, no time-of-day branching beyond `snapshot_at` / `discovered_at` / `updated_at`.
- Polymarket Gamma API is public and unauthenticated. Do not add API key handling. Do not store any auth tokens. Do not call private/CLOB Polymarket endpoints.
- If the Polymarket Gamma response shape is ambiguous, prefer defensive parsing: missing fields → `None`, unknown categories → `"other"`. Never raise on malformed market dicts; record the failure in `source_health` and continue.
- If you find yourself wanting to change anything listed under "Out of scope", stop and ask the user.

## Commit

Single commit. No merge, no rebase, no force-push.

Title (exact):

```
feat(gatto): add Phase F polymarket ingestion + universe discovery
```

Body: short bullet list — `ingestion/polymarket.py` real impl, `_run_polymarket*` wired in `run.py`, `polymarket:` config block, verify extended to 19 checks, README updates. Append the standard trailer to match prior Phase commits:

```
Co-authored-by: Cursor <cursoragent@cursor.com>
```

## Minor known follow-up (not Phase F scope)

`analysis/radar.py` currently lists `_ACTION_BLOCK_ORDER = (POSSIBLE_TRADE, WATCH, AVOID)` but `analysis/opportunities.py` also produces `INVESTIGATE` and `NO_EDGE` actions. The radar handles these via an alphabetical fallback, which happens to place `INVESTIGATE` before `WATCH` — semantically correct, but by accident, not design. Do NOT fix this in Phase F. It will be Phase E.1 follow-up (one line: insert `INVESTIGATE` between `POSSIBLE_TRADE` and `WATCH` in the tuple, plus one verify check). Leave it alone for now.
