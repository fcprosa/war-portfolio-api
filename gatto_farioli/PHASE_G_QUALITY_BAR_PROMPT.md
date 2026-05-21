
## 0. Pre-flight — run these FIRST and quote the output back to the user before writing any code

Per repo-root `AGENTS.md` rule 1:

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git stash list
```

Expected before you start: branch `main`, HEAD `4fc8535` (Phase F polymarket), working tree clean except untracked `.claude/`, `AGENTS.md`, `PRODUCT_VISION.md`, and the `gatto_farioli/PHASE_*.md` prompt files. Stash entry `wip: future-phase radar narratives kalshi` present.

If the pre-flight does not match, **stop and ask the user before touching any file**.

## 1. Context — read these in full before writing code

- `PRODUCT_VISION.md` — sections 7 (Quality Bar) and 10.1 (Phase P1 Reliable Analyst). This phase is the direct expression of section 7's downgrade rule.
- `AGENTS.md` — every rule applies. Especially: one phase = one commit, no `git stash pop`, run the four "before finishing" commands, and finish with the required output format.
- `gatto_farioli/analysis/opportunities.py` — Phase D module you will extend. Read the `_Candidate` dataclass, the action constants (`NO_EDGE`, `WATCH`, `INVESTIGATE`, `AVOID`, `POSSIBLE_TRADE`), and the existing gate logic in full.
- `gatto_farioli/analysis/radar.py` — Phase E renderer you will extend in the top-opportunities block.
- `gatto_farioli/storage/db.py` — see `_migrate_opportunity_candidates` and `_OPPORTUNITY_V2_COLUMNS`. You will add a sibling additive migration `_upgrade_opportunity_candidates_to_v3` that runs `ALTER TABLE ... ADD COLUMN` per new column, idempotently.
- `gatto_farioli/storage/schema.py` — you will add 5 new columns to the `opportunity_candidates` CREATE statement (for fresh DBs).
- `gatto_farioli/storage/source_health.py` — used to compute `data_health_ok`.
- `gatto_farioli/scripts/verify.py` — current 19-check harness you will extend.

## 2. Goal

Implement PRODUCT_VISION section 7 verbatim:

> A recommendation is valid only if it has: Evidence from stored data, Clear catalyst path, Defined invalidation, Risk/reward asymmetry, Executable instrument, Data health check passed. **If any of these are missing, downgrade to WATCH or NO_EDGE.**

Phase G makes each `opportunity_candidates` row carry its Quality Bar fields, persists them in SQLite, deterministically downgrades the action when the bar isn't met, and surfaces the new fields in the Daily Radar so Daniel can see WHY each row is rated as it is.

No LLM. No new ingestion. No new tables. No edits to ingestion modules. One commit.

## 3. In scope — exactly these changes, nothing more

### 3.1 Schema additions (additive only — preserve existing 163 rows)

Add five columns to `opportunity_candidates`. Update both:

- **`gatto_farioli/storage/schema.py`** — add the columns to the `CREATE TABLE IF NOT EXISTS opportunity_candidates` block so fresh DBs include them.
- **`gatto_farioli/storage/db.py`** — add `_upgrade_opportunity_candidates_to_v3(conn)` that:
  - reads `PRAGMA table_info(opportunity_candidates)`,
  - for each of the five new columns missing on an existing table, runs `ALTER TABLE opportunity_candidates ADD COLUMN <name> <type>`,
  - is idempotent and safe to run on a fresh DB (it will simply be a no-op after `CREATE TABLE`).
  - call `_upgrade_opportunity_candidates_to_v3(conn)` from `init_db` immediately after `conn.executescript(SCHEMA_SQL)`. Do not change `_migrate_opportunity_candidates` (the V2 downgrade safety check stays).

The five new columns, exact names and types:

| Column | Type | Meaning |
|---|---|---|
| `catalyst_path` | `TEXT` | One sentence naming the next concrete catalyst pulled from the dominant narrative. `NULL` if not derivable. |
| `invalidation_trigger` | `TEXT` | One sentence kill criterion derived from price/odds boundary. `NULL` if not derivable. |
| `risk_reward_summary` | `TEXT` | Human string like `"+12% / -5% (30d band)"`. `NULL` if not derivable. |
| `quality_bar_passed` | `INTEGER` | `0` or `1`. Cached result. |
| `quality_bar_missing` | `TEXT` | JSON array of strings naming which Quality Bar items are missing. Empty array `"[]"` when nothing is missing. |

Add no other columns. Do not add indexes on these columns. Do not rename or retype existing columns.

### 3.2 Quality Bar derivation in `analysis/opportunities.py`

Add a single new module-level function and a private downgrade step:

- **`compute_quality_bar(c: _Candidate, *, db_path: Path) -> dict`** — pure, deterministic. Returns a dict with keys `catalyst_path`, `invalidation_trigger`, `risk_reward_summary`, `executable_instrument`, `data_health_ok`, `missing_items`, `passed`. Reads from existing tables only: `narrative_clusters`, `news`, `prices`, `prediction_markets`, `market_universe`, `source_health`. No network.

  Derivation rules — exact:

  - **`catalyst_path`**: if `c.related_narrative_id` is set and the narrative is in `('active', 'emerging')`, set to `"<narrative.title> — status: <narrative.status>, articles in 24h: <article_count>"`. Else if `c.evidence` JSON has a `news` list with at least one item, set to `"news catalyst: <first_news.title>"`. Else `None`.
  - **`invalidation_trigger`**:
    - Equity (`c.related_ticker` set, no `c.related_market_ticker`): pull 30-day low and high from `prices`. If both available, set to `"close outside [<low>, <high>] (30d band on <ticker>)"`. Else `None`.
    - Kalshi/Polymarket (`c.related_market_ticker` set): pull latest `yes_price`/`no_price` from `prediction_markets` for that ticker. If found, set to `"yes_price outside [<yes-0.10>, <yes+0.10>] (current <yes>)"`. Else `None`.
    - Both empty: `None`.
  - **`risk_reward_summary`**:
    - Equity: with 30d band `[low, high]` and most recent close `px`, set to `"+<round((high-px)/px*100,1)>% / -<round((px-low)/px*100,1)>% (30d band)"`. Else `None`.
    - Prediction market: with `yes_price = y`, set to `"+<round((1-y)*100,1)>% if YES resolves / -<round(y*100,1)>% if NO resolves (current yes=<y>)"`. Else `None`.
  - **`executable_instrument`**: if `c.related_ticker` is set → `f"equity:{c.related_ticker}"`. Elif `c.related_market_ticker` is set → look up the platform in `market_universe` and return `f"{platform}:{symbol}"`; default to `f"market:{c.related_market_ticker}"` if not found. Else `None`.
  - **`data_health_ok`**: True iff (a) every news source backing this candidate (from `c.evidence.news[].source` if present) has `source_health.status != 'error'`, and (b) for prediction-market candidates, the platform's snapshot endpoint key (e.g. `kalshi:<ticker>` or `polymarket:gamma:markets`) is not `status='error'` in `source_health`. If no backing sources are listed at all, default to True (don't punish opportunities for missing source attribution — that's a separate concern).
  - **`missing_items`**: a list naming any of `["catalyst_path", "invalidation_trigger", "risk_reward_summary", "executable_instrument", "data_health_ok"]` that came out `None` or `False`. Note: `evidence_from_stored_data` is implicit (every candidate already has an `evidence` dict from Phase D) and so is not gated here.
  - **`passed`**: `len(missing_items) == 0`.

- **Downgrade step** — after all existing scoring/gating logic in `score_opportunities`, but before persistence, apply: if `passed == False`, cap `c.action` to at most `ACTION_WATCH`. Specifically, if current action is in `(ACTION_POSSIBLE_TRADE, ACTION_INVESTIGATE)`, set to `ACTION_WATCH`. Do not raise rows up. Do not change rows already at `WATCH`, `AVOID`, or `NO_EDGE`.

- **Persistence** — extend the existing `opportunity_candidates` upsert to write the five new columns:
  - `catalyst_path = quality_bar['catalyst_path']`
  - `invalidation_trigger = quality_bar['invalidation_trigger']`
  - `risk_reward_summary = quality_bar['risk_reward_summary']`
  - `quality_bar_passed = 1 if quality_bar['passed'] else 0`
  - `quality_bar_missing = json.dumps(quality_bar['missing_items'])`

Do not change the `evidence` JSON shape. Do not change existing action-gating thresholds. Do not introduce new action constants. Do not touch `ACTION_NO_EDGE`, `ACTION_AVOID`, etc.

### 3.3 Radar surface in `analysis/radar.py`

In the top-opportunities block, after each row's existing one-liner, emit up to three indented sub-lines when the field is non-null:

```
  • catalyst: <catalyst_path>
  • invalidate if: <invalidation_trigger>
  • R/R: <risk_reward_summary> | QB: <PASS|FAIL — missing: a, b>
```

(Use the literal bullet `•` so the sub-lines are visually distinct from the main `-` bullet.)

Add a new section **after** "Source-health warnings" and **before** "Missing-data flags":

```
## Quality bar exceptions
```

This section lists every row where `quality_bar_passed = 0` and the original (pre-downgrade) action would have been `POSSIBLE_TRADE` or `INVESTIGATE`. Since the downgrade is destructive (we lose the original action), surface this purely by `quality_bar_passed = 0 AND action = 'WATCH' AND score >= 50`. Each line: `<title> | downgraded to WATCH | missing: <comma-separated quality_bar_missing>`. If none qualify, emit `_no data_` per the established convention.

Do not change any other section. Do not change section order anywhere else. Do not change `_ACTION_BLOCK_ORDER`.

### 3.4 Tests

Extend `gatto_farioli/tests/test_opportunities.py` with **at least 5** new tests:

1. `compute_quality_bar` returns `passed=True` when narrative + 30d prices + healthy sources are all present for an equity candidate.
2. `compute_quality_bar` returns `passed=False` and lists `invalidation_trigger` in `missing_items` when no `prices` rows exist for the related ticker.
3. `compute_quality_bar` marks `data_health_ok=False` when a backing news source has `source_health.status='error'`.
4. `score_opportunities` downgrades a POSSIBLE_TRADE candidate to WATCH when the Quality Bar fails.
5. `score_opportunities` does NOT raise a WATCH candidate when the Quality Bar passes.

Extend `gatto_farioli/tests/test_radar.py` with **at least 2** new tests:

6. Radar output includes `catalyst:`, `invalidate if:`, and `R/R:` sub-lines for a row with all three populated.
7. Radar `## Quality bar exceptions` section lists a downgraded high-score candidate and prints `_no data_` when no rows qualify.

Total new tests: ≥ 7. Total suite size after Phase G: ≥ 46 (39 existing + 7 new).

### 3.5 Verify harness — 19 → 21

Append to `scripts/verify.py` in this order:

- **20. `quality bar downgrades POSSIBLE_TRADE to WATCH when invalidation missing`** — insert a narrative + a news row + an `opportunity_candidates` row with score=90, no `prices` row for the ticker; run `score_opportunities`; assert the resulting `action = 'WATCH'` and `quality_bar_passed = 0` and `"invalidation_trigger"` in the JSON-decoded `quality_bar_missing`.
- **21. `radar surfaces quality bar fields and exceptions`** — insert two opportunity rows (one QB-passed with non-null `catalyst_path`/`invalidation_trigger`/`risk_reward_summary`; one QB-failed at score 70 downgraded to WATCH); generate the radar; assert the literal strings `catalyst:`, `invalidate if:`, `R/R:`, and `## Quality bar exceptions` are present and the failed row's title appears under the exceptions section.

Final summary line must read exactly: `Verify: 21/21 passed.`

### 3.6 Docs

- `gatto_farioli/README.md` — add a Phase G row to the "What works today" table (capability: "Quality Bar enrichment (Phase G)"; module: `analysis/opportunities.py`; notes: "catalyst path / invalidation / risk-reward / data-health on every candidate; rows that miss the bar are deterministically capped at WATCH per PRODUCT_VISION §7"). Bump verify count to 21 and append checks 20 and 21.
- `README.md` (root) — bump verify count to 21 and append "Quality Bar enrichment per PRODUCT_VISION §7 — recommendations missing catalyst / invalidation / risk-reward / data-health are downgraded to WATCH" to the working-on-main list.

## 4. Out of scope — do NOT do any of these

- No LLM. No Anthropic / OpenAI / any model API call. Every Quality Bar field is computed deterministically from SQLite reads.
- No new ingestion sources. No PortWatch. No Twitter. No Filings. No FRED expansion.
- No edits to: `ingestion/*` (any file), `analysis/brief.py`, `analysis/narratives.py`, `analysis/news_score.py`, `analysis/thesis.py`, `analysis/delta.py`, `analysis/alerts.py`, `storage/state.py`, `storage/source_health.py` public API.
- No new action constants. `ACTION_EXECUTE_NOW` from PRODUCT_VISION §3.2 is **explicitly deferred** and must not be introduced now.
- No new tables. No new indexes. No retyping or renaming of existing columns.
- No changes to the `evidence` JSON shape produced by Phase D.
- No dashboard / Vercel / `api/` / `lib/` / `index.html` / `sw.js` edits.
- No dependency additions to `requirements.txt`.
- No edits to `config.yaml` beyond what is strictly required (this phase should not need any — if you find you need a config knob, stop and ask the user).
- No edits to `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`.
- Do not `git stash pop`, `git stash apply`, or read from `stash@{0}`. AGENTS.md rule 2.
- Do not mix this phase with any other work. AGENTS.md rule 2.

## 5. Allowed file list — `git diff --stat` after the commit must show only these paths

1. `gatto_farioli/storage/schema.py`
2. `gatto_farioli/storage/db.py`
3. `gatto_farioli/analysis/opportunities.py`
4. `gatto_farioli/analysis/radar.py`
5. `gatto_farioli/scripts/verify.py`
6. `gatto_farioli/tests/test_opportunities.py`
7. `gatto_farioli/tests/test_radar.py`
8. `gatto_farioli/README.md`
9. `README.md`

If you find yourself wanting to touch any path outside this list, stop and ask the user.

## 6. Definition of done — per AGENTS.md rule 3, run all four and quote their tail output before committing

```bash
cd gatto_farioli
python -m pytest -q                                              # ≥ 46 passed
python scripts/verify.py                                         # last line: "Verify: 21/21 passed."
cd ..
python3 -m gatto_farioli.run --health                            # exits 0, no tracebacks
git status --short                                               # only the 9 allowed paths show as modified
```

Additionally, before committing:

```bash
python3 -m gatto_farioli.run --radar --no-ingest                 # radar prints Quality bar exceptions section
git diff --stat                                                  # only the 9 allowed paths
```

If any of those fail, fix before committing. Do not weaken assertions or skip tests.

## 7. Commit

Single commit. No merge, rebase, or force-push. Title (exact):

```
feat(gatto): add Phase G quality bar enrichment per PRODUCT_VISION
```

Body — short bullet list referencing the schema additions, the downgrade rule, the radar surface, and the verify extension. Append the standard trailer:

```
Co-authored-by: Cursor <cursoragent@cursor.com>
```

## 8. Required completion report — per AGENTS.md rule 4

After the commit lands, reply to the user with exactly this structure (no extra preamble):

```
Branch + HEAD:
  main @ <new commit short sha> — "<commit title>"

Files changed:
  <list of paths from git show --stat>

Commands run + results:
  pytest -q                        → <tail line>
  scripts/verify.py                → Verify: 21/21 passed.
  python -m gatto_farioli.run --health → <one-line summary>
  git status --short               → <output>

Risks:
  <2–4 concise bullets — what could break, what assumptions you made, what migrations on an existing argos.db could surprise the user>

Next step:
  <one sentence proposing the natural Phase H, e.g. "Phase H — outcome tracking & calibration: persist each radar's POSSIBLE_TRADE rows to a new outcomes table and resolve them against price/market movement after N days, feeding back into confidence scoring.">
```

Do not skip the Risks section. Do not skip the Next step. Both are required by AGENTS.md rule 4.
