# Cursor prompt — Phase H: Outcome tracking & accountability (Learning-Layer foundation)

Paste everything below this line into Cursor. Do not edit the guardrails.

---

## 0. Pre-flight — run these FIRST and quote the output back to the user before writing any code

Per repo-root `AGENTS.md` rule 1:

```bash
git branch --show-current
git rev-parse HEAD
git status --short
git stash list
```

Expected before you start: branch `main`, HEAD `67fdd9f` (Phase G quality bar). Working tree clean except untracked `.claude/`, `AGENTS.md`, `PRODUCT_VISION.md`, and the `gatto_farioli/PHASE_*.md` prompt files. Stash entry `wip: future-phase radar narratives kalshi` present.

If the pre-flight does not match, **stop and ask the user before touching any file**.

## 1. Context — read these in full before writing code

- `PRODUCT_VISION.md` — sections 3.1 step 9 ("Post-mortem every closed decision"), 8 ("Always accountable: What I said vs what happened"), 9 (the Learning Layer), 10.3 (P3 autonomous pilot, outcome-based self-calibration), 11 (success metrics — every one of them requires this data).
- `AGENTS.md` — every rule. One phase = one commit, no `git stash pop`, run the four "before finishing" commands, finish with the required output format.
- `gatto_farioli/analysis/opportunities.py` — read the `_Candidate` dataclass and the new Phase G fields (`catalyst_path`, `invalidation_trigger`, `risk_reward_summary`, `quality_bar_passed`, `quality_bar_missing`, plus the existing `executable_instrument` derivation in `compute_quality_bar`). Phase H consumes these but must not modify the module.
- `gatto_farioli/analysis/radar.py` — Phase E/G renderer you will extend with ONE new section.
- `gatto_farioli/storage/schema.py` and `gatto_farioli/storage/db.py` — see the `_migrate_opportunity_candidates` / `_upgrade_opportunity_candidates_to_v3` migration pattern. Phase H adds a new top-level table — no migration needed, just a `CREATE TABLE IF NOT EXISTS` in `SCHEMA_SQL`.
- `gatto_farioli/run.py` — see how `_run_kalshi` and `_run_opportunities` are wired into `run_ingestion`. Mirror that for the new outcome runners.
- `gatto_farioli/scripts/verify.py` — current 21-check harness you will extend.
- `gatto_farioli/config.yaml` — see the `kalshi:` and `polymarket:` optional blocks for the pattern of "all keys optional, code provides defaults".

## 2. Goal

Make Gatto accountable. Every `POSSIBLE_TRADE` and `INVESTIGATE` row emitted by the scorer gets snapshotted into a new `opportunity_outcomes` table with the price/odds at emission. After a configurable window (default 7 days) those rows are deterministically resolved — `hit` / `miss` / `neutral` / `unresolvable` — by reading current prices and prediction-market odds. The Daily Radar surfaces a "Recent track record" section so Daniel sees rolling hit-rate every morning.

This phase is purely the **measurement infrastructure**. It does not yet feed back into scoring. Calibration (using realized hit-rate to adjust `confidence`) is explicitly deferred to a future Phase I.

No LLM. No new ingestion sources. No edits to scoring. One commit.

## 3. In scope — exactly these changes, nothing more

### 3.1 New table `opportunity_outcomes`

Add a `CREATE TABLE IF NOT EXISTS opportunity_outcomes` block to `gatto_farioli/storage/schema.py` with exactly these columns and types:

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | autoincrement |
| `candidate_key` | `TEXT NOT NULL` | matches `opportunity_candidates.candidate_key`; no FK constraint (the candidate may close or change) |
| `snapshot_at` | `TIMESTAMP NOT NULL` | UTC ISO when the outcome row was created |
| `action_at_emission` | `TEXT NOT NULL` | the action the candidate had at snapshot time (`POSSIBLE_TRADE` or `INVESTIGATE`) |
| `score_at_emission` | `REAL` | candidate score at snapshot time |
| `confidence_at_emission` | `REAL` | candidate confidence at snapshot time |
| `instrument_kind` | `TEXT` | one of `equity`, `kalshi`, `polymarket`, or `unknown` |
| `instrument_symbol` | `TEXT` | the ticker / market ticker / condition id at snapshot time |
| `entry_price` | `REAL` | latest equity close OR latest prediction-market `yes_price` at snapshot time; `NULL` if unavailable |
| `resolution_window_days` | `INTEGER NOT NULL DEFAULT 7` | how many days after `snapshot_at` to attempt resolution |
| `resolved_at` | `TIMESTAMP` | when resolution ran; `NULL` while open |
| `exit_price` | `REAL` | price/odds at resolution time; `NULL` while open or unresolvable |
| `realized_return` | `REAL` | for equity: `(exit_price - entry_price) / entry_price` (decimal). For prediction markets: `exit_yes_price - entry_yes_price` (raw price-point delta in [-1, 1]). `NULL` while open. |
| `resolution_status` | `TEXT NOT NULL DEFAULT 'open'` | one of `open`, `resolved_hit`, `resolved_miss`, `resolved_neutral`, `unresolvable` |
| `notes` | `TEXT` | free-text human note, e.g. `"no prices row within window"` |

Add these indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_outcomes_candidate ON opportunity_outcomes(candidate_key);
CREATE INDEX IF NOT EXISTS idx_outcomes_status ON opportunity_outcomes(resolution_status);
CREATE INDEX IF NOT EXISTS idx_outcomes_snapshot ON opportunity_outcomes(snapshot_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outcomes_candidate_day
  ON opportunity_outcomes(candidate_key, date(snapshot_at));
```

The unique index on `(candidate_key, date(snapshot_at))` enforces "one snapshot per candidate per UTC day" — re-running `--outcomes` later the same day must be idempotent.

No migration helper needed. `init_db` already runs `conn.executescript(SCHEMA_SQL)`; the `CREATE TABLE IF NOT EXISTS` is sufficient for both fresh and existing DBs.

### 3.2 New module `gatto_farioli/analysis/outcomes.py`

Pure module, no network. Public surface:

```python
@dataclass(frozen=True)
class OutcomeSnapshotResult:
    candidates_seen: int
    rows_created: int
    rows_skipped_existing: int
    rows_skipped_missing_price: int

@dataclass(frozen=True)
class OutcomeResolveResult:
    rows_examined: int
    rows_resolved_hit: int
    rows_resolved_miss: int
    rows_resolved_neutral: int
    rows_resolved_unresolvable: int
    rows_still_open: int

def snapshot_open_opportunities(
    cfg: dict, db_path: Path, *, dry_run: bool = False, now: datetime | None = None
) -> OutcomeSnapshotResult: ...

def resolve_open_outcomes(
    cfg: dict, db_path: Path, *, dry_run: bool = False, now: datetime | None = None
) -> OutcomeResolveResult: ...
```

Implementation rules — exact:

**`snapshot_open_opportunities`:**

- Select every row from `opportunity_candidates` where `action IN ('POSSIBLE_TRADE', 'INVESTIGATE')` AND `status = 'open'`.
- For each, parse the Phase-G-style `executable_instrument` value via SQL or via `c.related_ticker` / `c.related_market_ticker`:
  - If `related_ticker` is set and `related_market_ticker` is null → `instrument_kind='equity'`, `instrument_symbol=related_ticker.upper()`.
  - If `related_market_ticker` is set → look up `market_universe` to determine `instrument_kind` (`kalshi` or `polymarket`); `instrument_symbol=related_market_ticker`. If not found, default `instrument_kind='unknown'`.
  - If both are null → skip the candidate (do not create an outcome row).
- For equity: `entry_price` = most recent `prices.close` for that ticker, ORDER BY `date DESC LIMIT 1`. If none, set `entry_price=NULL` and record a row with `notes='no equity price at snapshot'`.
- For kalshi/polymarket: `entry_price` = most recent `prediction_markets.yes_price` for `(platform, ticker) = (instrument_kind, instrument_symbol)`, ORDER BY `snapshot_at DESC LIMIT 1`. If none, set `entry_price=NULL` and record a row with `notes='no prediction-market price at snapshot'`.
- Insert into `opportunity_outcomes` using `INSERT OR IGNORE` against the `(candidate_key, date(snapshot_at))` unique index, so re-running within the same UTC day is a no-op.
- `resolution_window_days` comes from `cfg.get('outcomes', {}).get('resolution_window_days', 7)`.
- `action_at_emission`, `score_at_emission`, `confidence_at_emission` copied directly from the candidate row.
- Honour `dry_run` — compute everything but skip writes.

**`resolve_open_outcomes`:**

- Select rows from `opportunity_outcomes` where `resolution_status='open'` AND `datetime(snapshot_at, '+' || resolution_window_days || ' days') <= :now`.
- For each:
  - **Equity**: `exit_price` = most recent `prices.close` for the symbol where `date <= :now` ORDER BY `date DESC LIMIT 1`. If `entry_price` was NULL or `exit_price` is NULL → `resolution_status='unresolvable'`, `notes='no exit price'`. Otherwise compute `realized_return = (exit_price - entry_price) / entry_price`. Resolution classification (defaults configurable):
    - `resolved_hit` if `realized_return >= equity_hit_threshold_pct/100` (default 5.0%, i.e. 0.05).
    - `resolved_miss` if `realized_return <= -equity_hit_threshold_pct/100`.
    - `resolved_neutral` otherwise.
  - **Kalshi / Polymarket**: `exit_price` = most recent `prediction_markets.yes_price` for the same `(platform, ticker)` where `snapshot_at <= :now`. If unavailable → `unresolvable`. Otherwise `realized_return = exit_price - entry_price` (raw price-point delta in [-1, 1]). Classification:
    - `resolved_hit` if `realized_return >= prediction_market_hit_threshold_pp/100` (default 10pp, i.e. 0.10).
    - `resolved_miss` if `realized_return <= -prediction_market_hit_threshold_pp/100`.
    - `resolved_neutral` otherwise.
  - **`unknown` instrument_kind**: `resolution_status='unresolvable'`, `notes='unknown instrument kind'`.
- Write `resolved_at`, `exit_price`, `realized_return`, `resolution_status`, `notes` back to the row.
- Honour `dry_run`.

Config defaults (read once at function entry, all optional):

```yaml
outcomes:
  resolution_window_days: 7
  equity_hit_threshold_pct: 5.0
  prediction_market_hit_threshold_pp: 10.0
  recent_window_days: 14
```

Add this `outcomes:` block to `gatto_farioli/config.yaml` with these exact defaults. The module must still work if the block is absent — defaults are baked into the code.

### 3.3 CLI wiring in `gatto_farioli/run.py`

- Add two new internal runners `_run_outcomes_snapshot(config, args)` and `_run_outcomes_resolve(config, args)`, each mirroring the shape of `_run_opportunities` (return `(status, message)`; record into `runs` table via the existing helper).
- Wire BOTH into the default `--ingest` pipeline, **after** the existing `_run_opportunities` step. Order: opportunities → outcomes_snapshot → outcomes_resolve.
- Add a new `--outcomes` flag that runs snapshot + resolve **only**, with no other ingestion. Honors `--dry-run`.
- `--outcomes --dry-run` must do zero writes (no `briefs`, no `opportunity_outcomes`, no `runs` row).

### 3.4 Radar surface in `gatto_farioli/analysis/radar.py`

Add ONE new section, placed **after** `## Quality bar exceptions` and **before** `## Source-health warnings`. Title: `## Recent track record`.

Content (deterministic):

- Read the last `recent_window_days` (default 14) days of resolved rows: `SELECT * FROM opportunity_outcomes WHERE resolution_status LIKE 'resolved_%' AND resolved_at >= :cutoff ORDER BY resolved_at DESC`.
- If zero resolved rows in window: emit `_no data_` and stop.
- Otherwise, emit:
  1. A summary line: `Resolved last <N>d: <total> outcomes — hit <H> / miss <M> / neutral <X> — avg realized return <pct>`. For equity outcomes only, compute the avg realized return as a percentage; prediction-market deltas average separately and are reported in a second sub-line if any prediction-market rows are present.
  2. Up to the 5 most recent resolved rows as bullets: `- <candidate_key> | <action_at_emission> | <instrument_kind>:<instrument_symbol> | snapshot=<snapshot_at iso date only> resolved=<resolved_at iso date only> | realized=<formatted> | <resolution_status>`.

Do not modify any other section. Do not change section ordering anywhere else. Do not touch `_ACTION_BLOCK_ORDER`.

### 3.5 Tests

Add a new file `gatto_farioli/tests/test_outcomes.py` with **at least 7** tests, all offline:

1. `snapshot_open_opportunities` inserts one row per POSSIBLE_TRADE candidate that has `related_ticker` and a price row.
2. `snapshot_open_opportunities` skips a second call within the same UTC day (idempotent via unique index).
3. `snapshot_open_opportunities` writes `entry_price=NULL` with `notes` containing `"no equity price"` when no `prices` row exists.
4. `resolve_open_outcomes` marks a row `resolved_hit` when realized return is at or above the equity threshold.
5. `resolve_open_outcomes` marks a row `resolved_miss` when realized return is at or below the negative equity threshold.
6. `resolve_open_outcomes` marks a row `unresolvable` when `entry_price` is NULL.
7. `resolve_open_outcomes` leaves a row with `resolution_status='open'` when `snapshot_at + window > now`.

Extend `gatto_farioli/tests/test_radar.py` with **at least 2** new tests:

8. Radar `## Recent track record` section renders the summary line + bullets when ≥1 resolved row is present.
9. Radar `## Recent track record` section renders `_no data_` when no resolved rows exist within the configured window.

Total new tests: ≥ 9. Suite size after Phase H: ≥ 57 (48 existing + 9 new).

### 3.6 Verify harness — 21 → 24

Append to `scripts/verify.py` in this order, after check 21:

- **22. `outcomes snapshot creates row for POSSIBLE_TRADE candidate`** — insert a candidate with `action='POSSIBLE_TRADE'`, `related_ticker='VFY'`, a `prices` row for `VFY`; call `snapshot_open_opportunities`; assert exactly one `opportunity_outcomes` row exists with `instrument_kind='equity'` and non-null `entry_price`.
- **23. `outcomes resolve classifies hit vs miss correctly`** — insert two open outcomes for the same ticker, one with `entry_price=100` and a later `prices` row at `108` (≥+5% → hit), one with `entry_price=100` and a later `prices` row at `92` (≤-5% → miss); advance the clock past the window via the `now=` argument; call `resolve_open_outcomes`; assert `resolved_hit` and `resolved_miss` are both set.
- **24. `radar surfaces recent track record summary`** — seed two resolved outcomes (one hit, one miss); generate the radar; assert the literal strings `## Recent track record`, `hit 1`, `miss 1` are present.

Final summary line must read exactly: `Verify: 24/24 passed.`

### 3.7 Docs

- `gatto_farioli/README.md` — add a Phase H row to the "What works today" table (capability: "Outcome tracking & accountability (Phase H)"; module: `analysis/outcomes.py` + `opportunity_outcomes` table; notes: "snapshots every POSSIBLE_TRADE/INVESTIGATE at emission, resolves after 7d (configurable) against price/odds, surfaces rolling hit-rate in the radar"). Bump verify count to 24 and append checks 22–24. Add `python run.py --outcomes` to the "Daily commands" block.
- `README.md` (root) — bump verify count to 24 and append "Outcome tracking & rolling hit-rate per PRODUCT_VISION §8 — every POSSIBLE_TRADE/INVESTIGATE is snapshotted at emission and resolved after a 7d window" to the working-on-main list.

## 4. Out of scope — do NOT do any of these

- **No calibration.** Do not feed realized hit-rate back into `confidence` in `opportunities.py`. Phase I will do that. Phase H is measurement infrastructure only.
- **No new action constants.** `ACTION_EXECUTE_NOW` from PRODUCT_VISION §3.2 is still deferred.
- **No LLM**, no Anthropic / OpenAI / any model API.
- **No new ingestion sources.** No PortWatch, Twitter, Filings, FRED expansion.
- **No edits to `analysis/opportunities.py`**, `analysis/narratives.py`, `analysis/news_score.py`, `analysis/thesis.py`, `analysis/delta.py`, `analysis/alerts.py`, `analysis/brief.py`. Phase H reads from `opportunity_candidates` but never writes to it.
- **No edits to `ingestion/*`** at all.
- **No edits to `storage/db.py`** (the new table goes in `storage/schema.py` only; `init_db` picks it up via the existing `executescript(SCHEMA_SQL)` call).
- **No edits to `storage/state.py`**, `storage/source_health.py`.
- **No schema changes to `opportunity_candidates`** — read-only consumer.
- **No new indexes on existing tables.** Only the four indexes specified in §3.1 on the new table.
- **No dashboard / Vercel / `api/` / `lib/` / `index.html` / `sw.js` edits.**
- **No dependency additions to `requirements.txt`.**
- **No edits to `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`.**
- **Do not `git stash pop`, `git stash apply`, or read from `stash@{0}`.** AGENTS.md rule 2.
- **Do not mix this phase with any other work.** AGENTS.md rule 2.

## 5. Allowed file list — `git diff --stat` after the commit must show only these paths

1. `gatto_farioli/storage/schema.py`
2. `gatto_farioli/analysis/outcomes.py` (new file)
3. `gatto_farioli/analysis/radar.py`
4. `gatto_farioli/run.py`
5. `gatto_farioli/config.yaml`
6. `gatto_farioli/scripts/verify.py`
7. `gatto_farioli/tests/test_outcomes.py` (new file)
8. `gatto_farioli/tests/test_radar.py`
9. `gatto_farioli/README.md`
10. `README.md`

If you find yourself wanting to touch any path outside this list, stop and ask the user.

## 6. Definition of done — per AGENTS.md rule 3, run all four before committing

```bash
cd gatto_farioli
python -m pytest -q                                              # ≥ 57 passed
python scripts/verify.py                                         # last line: "Verify: 24/24 passed."
cd ..
python3 -m gatto_farioli.run --health                            # exits 0, opportunity_outcomes row count printed
git status --short                                               # only the 10 allowed paths show as modified
```

Plus these targeted smoke checks:

```bash
python3 -m gatto_farioli.run --outcomes --dry-run                # zero writes; prints snapshot+resolve plan
python3 -m gatto_farioli.run --outcomes                          # writes opportunity_outcomes rows
python3 -m gatto_farioli.run --radar --no-ingest                 # radar prints "## Recent track record" section
git diff --stat                                                  # only the 10 allowed paths
```

If any check fails, fix it before committing. Do not weaken assertions or skip tests.

## 7. Commit

Single commit. No merge, rebase, or force-push. Title (exact):

```
feat(gatto): add Phase H outcome tracking and accountability
```

Body — short bullet list referencing the new `opportunity_outcomes` table, the `analysis/outcomes.py` snapshot+resolve module, the `--outcomes` CLI, the radar's `## Recent track record` section, and the verify extension to 24. Append the standard trailer:

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
  pytest -q                            → <tail line>
  scripts/verify.py                    → Verify: 24/24 passed.
  python -m gatto_farioli.run --health → <one-line summary including opportunity_outcomes count>
  python -m gatto_farioli.run --outcomes → <SnapshotResult + ResolveResult summary>
  python -m gatto_farioli.run --radar --no-ingest → <one-line summary noting Recent track record section appeared>
  git status --short                   → <output>

Risks:
  <2–4 concise bullets — e.g. data lag on resolution if user runs --outcomes only once a week; equity vs prediction-market threshold asymmetry; the unique-per-day index means manual time travel in tests requires the `now=` argument; whether the radar section helps or distracts when track record is thin>

Next step:
  <one sentence proposing Phase I, which should be confidence calibration that consumes Phase H data: e.g. "Phase I — confidence calibration: blend each candidate's prior realized hit-rate (by source_type and instrument_kind) into the existing confidence score in opportunities.py, with a strict minimum-sample floor before calibration takes effect.">
```

Do not skip the Risks section. Do not skip the Next step. Both are required by AGENTS.md rule 4.

## 9. Known follow-up — not Phase H scope

The radar's `_ACTION_BLOCK_ORDER` in `analysis/radar.py` still lists only `(POSSIBLE_TRADE, WATCH, AVOID)`. `analysis/opportunities.py` produces `INVESTIGATE` and `NO_EDGE` too. The current alphabetical fallback places `INVESTIGATE` before `WATCH` — semantically correct but accidental. Do **not** fix this in Phase H. It is a one-line follow-up that will land in a tiny dedicated commit later.
