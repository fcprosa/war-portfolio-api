# Cursor prompt — Phase E: Daily Radar (`analysis/radar.py`)

Paste everything below this line into Cursor. Do not edit the guardrails.

---

## Context (read first; do not modify)

Repo: `war-portfolio-api`. Work only inside `gatto_farioli/` unless a README change is explicitly required.

Current `main` (HEAD `33a18f7`) ships:

- Session 1 foundation + hardening
- Phase A — narratives (`analysis/narratives.py`, `narrative_clusters` table, statuses `emerging` / `active` / `fading` / `resolved`)
- Phase B — Kalshi market universe (`ingestion/kalshi.py` discovery → `market_universe`, sports excluded by default)
- Phase C — source health (`storage/source_health.py`, `source_health` table)
- Phase D foundation — strict opportunity scoring (`analysis/opportunities.py`, `opportunity_candidates` table with hard `POSSIBLE_TRADE` gates)
- Daily Edge Brief v1 — `analysis/brief.py` writes `briefs` with `type='daily_edge_v1'`
- 15-check verify harness; 25 pytests; all green.

There is **no `analysis/radar.py` on `main`**. A local `git stash` entry (`stash@{0}: On main: wip: future-phase radar narratives kalshi`) contains an earlier radar attempt mixed with unrelated Kalshi / user-state work. **Do NOT `git stash pop`, `git stash apply`, or read from that stash. Start fresh.**

## Goal

Ship Phase E: a deterministic **Daily Radar** that consumes only existing tables and surfaces the most actionable signals right now. No new ingestion sources. No LLM. Output is markdown stored in `briefs` with `type='edge_radar_v1'`.

## In scope

1. **New module `gatto_farioli/analysis/radar.py`** exposing:
   - `generate_daily_radar(cfg: dict, db_path: Path, dry_run: bool = False) -> str`
   - Returns markdown radar text. Unless `dry_run`, writes exactly one row to `briefs` (`type='edge_radar_v1'`) and one row to `runs` (`module='radar'`).
   - Reads only from: `opportunity_candidates`, `narrative_clusters`, `market_universe`, `positions`, `news`, `prices`, `source_health`, `runs`. Writes only to `briefs` and `runs`.

2. **Radar sections (in this exact order, all deterministic, all headers present even when empty):**
   1. **Header** — generated_at (UTC ISO), top-line ingestion staleness summary (`OK` vs. `STALE`).
   2. **Top opportunities** — top `N=10` rows from `opportunity_candidates` ordered by `score DESC, confidence DESC, candidate_key ASC`. Group by `action`: `POSSIBLE_TRADE` block first, then `WATCH`, then `AVOID`. Each row: title, score, confidence, action, related_ticker / related_market_ticker, signals_count, one-line evidence summary.
   3. **Active & emerging narratives** — `narrative_clusters` where `status IN ('active','emerging')`, ordered `momentum_24h DESC, last_seen DESC`, capped at `narrative_max=8`. Each row: title, status, article_count, momentum_24h, sectors, related_tickers, related_markets.
   4. **Position-aware callouts** — for each row in `positions`, surface intersections with: (a) `POSSIBLE_TRADE` opportunities sharing `related_ticker`, and (b) active/emerging narratives whose `related_tickers` or `sectors` overlap the position's `ticker`/`thesis`.
   5. **Source-health warnings** — `storage.source_health.list_unhealthy(db_path=...)`. One line per source.
   6. **Missing-data flags** — list modules whose latest `runs` row is `error` OR older than `staleness_hours=36`.

3. **CLI flag in `gatto_farioli/run.py`:**
   - Add `--radar` (mirroring `--brief` shape).
   - Runs full ingestion first unless `--no-ingest`. Respects `--dry-run`.
   - `python -m gatto_farioli.run --radar --no-ingest` must do zero network calls.
   - `python -m gatto_farioli.run --radar --dry-run --no-ingest` must print the radar without writing to `briefs` or `runs`.

4. **Optional `radar:` section in `config.yaml`** with keys `top_n`, `staleness_hours`, `narrative_max`. Radar must work with the section absent (defaults: 10 / 36 / 8). If you add the section to the shipped `config.yaml`, use the defaults; do not invent new tunables.

5. **Tests — new file `gatto_farioli/tests/test_radar.py` with ≥ 5 unit tests:**
   1. radar header includes a UTC timestamp and the literal `edge_radar_v1` label
   2. empty DB renders every section header without raising
   3. top-opportunities block respects `top_n` and orders by `score DESC, confidence DESC`
   4. `POSSIBLE_TRADE` rows appear textually before `WATCH`, which appears before `AVOID`
   5. a position whose `ticker` matches an active narrative's `related_tickers` triggers a callout line

6. **Verify harness — extend `gatto_farioli/scripts/verify.py` from 15 → 17 checks**, appended in this order after the existing 15:
   - **16. `radar generation does not crash on empty db`** — call `generate_daily_radar(cfg, tmp_db, dry_run=True)`; assert the return value is a string containing `Top opportunities`, `Active & emerging narratives`, and `Source-health warnings`.
   - **17. `radar separates POSSIBLE_TRADE from WATCH/AVOID`** — insert two `opportunity_candidates` rows (one `POSSIBLE_TRADE`, one `WATCH`); assert the `POSSIBLE_TRADE` row's title appears at a lower string index than the `WATCH` row's title in the output.
   - Final summary line must read exactly: `Verify: 17/17 passed.`

7. **Docs:**
   - `gatto_farioli/README.md` — add a Phase E row to the "What works today" capability table; bump the integration-harness count to 17 and append checks 16 and 17; remove `No radar module.` from the "What is intentionally not in this build" list.
   - `README.md` (root) — append "Daily Radar (`analysis/radar.py`) over existing tables, stored as `edge_radar_v1`" to the "What's working on `main`" list; update verify count to 17; drop the "no `radar` module on `main` yet" sentence; keep the stash-not-applied note as-is.

## Out of scope — do NOT do any of these

- No new ingestion sources (no Polymarket, PortWatch, Twitter, Filings, LLM, Anthropic API).
- No changes to `analysis/brief.py`. Daily Edge Brief v1 stays exactly as-is.
- No changes to `analysis/opportunities.py` scoring, gating, action constants, or `_Candidate` shape.
- No changes to `analysis/narratives.py` clustering, status transitions, or sector logic.
- No changes to `ingestion/*.py` (Kalshi, news, prices, macro, polymarket stub, portwatch stub, twitter stub, filings stub).
- No changes to `analysis/news_score.py`, `analysis/thesis.py`, `analysis/delta.py`, `analysis/alerts.py`.
- No SQL schema migrations. No new tables. No `ALTER TABLE`. `briefs` and `runs` are reused.
- No changes to `storage/db.py` helpers, `storage/state.py`, `storage/source_health.py` public API, `storage/schema.py`.
- No dashboard / `index.html` / Vercel `api/` / `lib/` / `sw.js` changes.
- No Telegram, email, push, webhook, or file-export outputs.
- No changes to `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`.
- No dependency additions to `requirements.txt`.
- Do not `git stash pop`, `git stash apply`, or otherwise touch `stash@{0}`.
- Do not refactor, rename, or reorder anything outside `analysis/radar.py`, `run.py`, `scripts/verify.py`, `tests/test_radar.py`, `config.yaml` (optional `radar:` block only), and the two READMEs.

## Definition of done — all must pass before committing

```bash
cd gatto_farioli
python -m pytest -q                                           # >= 30 passed
python scripts/verify.py                                      # last line: "Verify: 17/17 passed."
python -m gatto_farioli.run --health                          # exits 0, no tracebacks
python -m gatto_farioli.run --radar --no-ingest --dry-run     # prints radar; briefs count unchanged
python -m gatto_farioli.run --radar --no-ingest               # prints radar; briefs count +1 with type='edge_radar_v1'
git diff --stat                                               # only allowed paths touched (see Out of scope)
```

If any check fails, fix before committing. Do not weaken assertions to make tests pass.

## Guardrails for the agent

- Before writing code, read: `analysis/brief.py`, `analysis/opportunities.py`, `analysis/narratives.py`, `storage/source_health.py`, `scripts/verify.py`, `run.py`. Mirror their patterns (function signatures, `dry_run` handling, `runs` row recording, `query_one`/`query_all` usage).
- Use `storage.db.get_conn` / `query_one` / `query_all`. Do not open raw `sqlite3.connect`.
- Use `datetime.now(timezone.utc)` for every timestamp. No naive datetimes anywhere.
- Output must be deterministic given the same DB state. No randomness. No `random`, no `uuid` (use stable `candidate_key` / `cluster_key` already in the DB).
- Sections with zero rows must still print their header followed by the literal `_no data_` placeholder.
- Any line that surfaces a number must show what it counts (e.g. `score=78.3`, `confidence=4.0`, `signals=3`, `articles=12`).
- If you find yourself wanting to change anything listed under "Out of scope", stop and ask the user.

## Commit

Single commit. No merge, no rebase, no force-push.

Title (exact):

```
feat(gatto): add Phase E daily radar over existing tables
```

Body: short bullet list — `analysis/radar.py` added, `--radar` CLI wired, verify extended to 17 checks, README updates. Append the standard trailer to match prior Phase commits:

```
Co-authored-by: Cursor <cursoragent@cursor.com>
```
