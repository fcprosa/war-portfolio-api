# Cursor prompt — Phase J: FRED Macro Ingestion

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

Expected before you start: branch `main`, HEAD `2f6e814` (Phase I LLM dialogue CLI). Stash entry `wip: future-phase radar narratives kalshi` present.

**The working tree will show two modified files from post-Phase-I fixups:**

```
 M gatto_farioli/analysis/dialogue.py
 M gatto_farioli/config.yaml
```

Before writing a single line of Phase J code, commit these two files as a separate fixup:

```bash
cd gatto_farioli
git add analysis/dialogue.py config.yaml
git commit -m "fix(gatto): post-Phase-I model-id fix and dialogue context enrichment"
```

After that commit, run `git status --short` again and confirm the tree is clean (only untracked `.claude/`, `AGENTS.md`, `CLAUDE.md`, `PRODUCT_VISION.md`, `PHASE_*.md` files remain). The new HEAD from this fixup commit is the base for Phase J.

If the working tree shows anything other than those untracked files after the fixup commit, **stop and ask the user before touching any file**.

## 1. Context — read these in full before writing code

- `PRODUCT_VISION.md` — sections 1 ("Continuously ingest global signals"), 3.1 step 1 ("Observe: news, price action, prediction markets, **macro**"), 4 (scope: politics, macro, commodities, technology, rates, weather, regulation, elections), 9 (Ingestion Layer: "News, market, **macro**, and prediction market snapshots with source health tracking").
- `AGENTS.md` — every rule. One phase = one commit, no `git stash pop`, run the four "before finishing" commands, finish with the required output format.
- `gatto_farioli/ingestion/macro.py` — the current no-op stub you will replace. Do not change the module's public name `ingest_macro`; the stub's signature is compatible with what you will implement.
- `gatto_farioli/storage/schema.py` — read the `macro` table definition: `(indicator TEXT, date DATE, value REAL, PRIMARY KEY (indicator, date))`. Phase J writes to this table. No schema changes needed.
- `gatto_farioli/storage/source_health.py` — `record_success` and `record_failure` signatures. Phase J calls these per-series, following the pattern in `ingestion/news.py` (per feed URL) and `ingestion/kalshi.py` (per ticker).
- `gatto_farioli/storage/db.py` — `get_conn`, `query_all`, `query_one`. Mirror these exactly.
- `gatto_farioli/run.py` — read `_run_prices`, `_run_kalshi`, and `run_ingestion` carefully. Mirror `_run_prices` for the new `_run_macro` shape. Insert `macro` into `run_ingestion` **after** `prices` and **before** `kalshi` — so macro data is fresh before opportunity scoring reads the DB.
- `gatto_farioli/analysis/dialogue.py` — read `_build_context` and `_serialize_context`. Phase J adds one new key (`"macro_snapshot"`) to the context dict and one new `## Macro snapshot` section to the serialized string.
- `gatto_farioli/config.yaml` — read the `kalshi:` and `polymarket:` optional blocks for the pattern of "all keys optional; code provides defaults". The new `fred:` block follows the same pattern.
- `gatto_farioli/.env` — do NOT read or log its contents. Know that `FRED_API_KEY=` is already a placeholder. If the key is absent or empty, the module must skip cleanly without error.
- `gatto_farioli/requirements.txt` — `fredapi>=0.5.2` is already listed. Do NOT add it again. Do NOT add any other dependency.
- `gatto_farioli/scripts/verify.py` — the 27-check harness you will extend to 30.

## 2. Goal

Fill the `macro: 0` gap. Implement real FRED macro ingestion so that every `--ingest` run populates the `macro` table with a curated set of rates, inflation, growth, commodity, dollar, and credit indicators. Enrich the `--ask` dialogue context so Daniel can ask "What are rates doing?" or "What is the macro backdrop for my thesis?" and get data-backed answers.

No new tables. No schema changes. No new dependencies. One commit.

If `FRED_API_KEY` is absent: skip cleanly and record `"skipped"` in `runs` — not an error. The rest of the pipeline must continue uninterrupted.

---

## 3. In scope — exactly these changes, nothing more

### 3.1 Replace `gatto_farioli/ingestion/macro.py`

Replace the entire file. The module must be importable with no side effects at import time.

#### Curated series list

Define as a module-level constant `_DEFAULT_FRED_SERIES: dict[str, str]` mapping FRED series ID → human label. Use exactly these 13 series, in this order:

```python
_DEFAULT_FRED_SERIES: dict[str, str] = {
    # Rates
    "DFF":            "Fed Funds Effective Rate",
    "DGS10":          "10-Year Treasury Yield",
    "DGS2":           "2-Year Treasury Yield",
    "T10Y2Y":         "10Y-2Y Yield Curve Spread",
    # Inflation
    "CPIAUCSL":       "CPI All Items",
    "CPILFESL":       "Core CPI (ex food & energy)",
    "T5YIE":          "5-Year Breakeven Inflation",
    # Growth / Labor
    "UNRATE":         "Unemployment Rate",
    "ICSA":           "Initial Jobless Claims (weekly)",
    # Commodities
    "DCOILWTICO":     "WTI Crude Oil Price",
    "DCOILBRENTEU":   "Brent Crude Oil Price",
    # Dollar
    "DTWEXBGS":       "Trade-Weighted US Dollar Index",
    # Credit
    "BAMLH0A0HYM2":   "HY Credit Spread (OAS)",
}

_DEFAULT_LOOKBACK_DAYS = 90
```

#### Result dataclass

```python
@dataclass(frozen=True)
class MacroIngestResult:
    series_attempted: int
    series_succeeded: int
    rows_upserted: int
    failures: list[dict]
    skipped: bool = False
    skip_reason: str = ""
```

#### `ingest_macro(config, db_path, *, dry_run=False) -> MacroIngestResult`

Implementation — exact steps:

1. **Read config.** Extract the optional `fred:` block:
   ```python
   fred_cfg = config.get("fred", {})
   lookback_days = int(fred_cfg.get("lookback_days", _DEFAULT_LOOKBACK_DAYS))
   series_map = fred_cfg.get("series_map", _DEFAULT_FRED_SERIES)
   # If config supplies a bare list instead of a dict, treat it as IDs with no label.
   if isinstance(series_map, list):
       series_map = {s: s for s in series_map}
   ```

2. **Load API key** via `python-dotenv` (already in requirements). Call `load_dotenv()` (reads from `gatto_farioli/.env`). Then:
   ```python
   api_key = os.environ.get("FRED_API_KEY", "").strip()
   if not api_key:
       return MacroIngestResult(
           series_attempted=0, series_succeeded=0, rows_upserted=0,
           failures=[], skipped=True, skip_reason="FRED_API_KEY not set",
       )
   ```

3. **Compute observation window.**
   ```python
   start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
   ```

4. **Instantiate FRED client.**
   ```python
   from fredapi import Fred
   fred = Fred(api_key=api_key)
   ```

5. **Fetch each series.** For each `(series_id, label)` in `series_map.items()`:
   - Wrap in `try/except Exception`:
     ```python
     data = fred.get_series(series_id, observation_start=start_date)
     # data is a pandas Series with DatetimeIndex; values may be NaN for missing obs
     ```
   - Drop NaN values: `data = data.dropna()`.
   - Build a list of `(series_id, date_str, float(value))` tuples where `date_str = idx.strftime("%Y-%m-%d")`.
   - If `dry_run`, skip writes but count rows.
   - If not `dry_run`, upsert into `macro` table:
     ```sql
     INSERT OR REPLACE INTO macro (indicator, date, value) VALUES (?, ?, ?)
     ```
     Use a single `executemany` inside `get_conn`. Count upserted rows.
   - On success: call `source_health.record_success(f"fred:{series_id}", f"{len(rows)} rows", db_path=db_path)`.
   - On exception: append `{"series": series_id, "label": label, "error": str(exc)}` to `failures`; call `source_health.record_failure(f"fred:{series_id}", str(exc), db_path=db_path)`. Continue to next series — do not abort the loop.

6. **Return result.**
   ```python
   return MacroIngestResult(
       series_attempted=len(series_map),
       series_succeeded=len(series_map) - len(failures),
       rows_upserted=total_rows,
       failures=failures,
   )
   ```

No prints inside the module. Callers handle logging.

---

### 3.2 Wire into `gatto_farioli/run.py`

**New runner** — add after `_run_prices` and before `_run_kalshi`:

```python
def _run_macro(config: dict, args: argparse.Namespace) -> tuple[str, str]:
    from ingestion.macro import ingest_macro
    from storage import source_health

    result = ingest_macro(config, db_path=args.db, dry_run=args.dry_run)
    if result.skipped:
        return "skipped", result.skip_reason
    message = (
        f"series {result.series_succeeded}/{result.series_attempted}, "
        f"rows {result.rows_upserted}, failures {len(result.failures)}"
    )
    for f in result.failures:
        print(f"  WARN macro series failed: {f['series']} — {f['error']}")
    status = "ok" if result.series_succeeded else "error"
    return status, message
```

**Insert into `run_ingestion`** — place the `macro` tuple **after** `prices` and **before** `kalshi`:

```python
("macro", _run_macro, False),
```

No other changes to `run.py`.

---

### 3.3 Enrich `gatto_farioli/analysis/dialogue.py`

Add one new key to `_build_context` and one new section to `_serialize_context`.

**In `_build_context`** — add after the `"positions"` query:

```python
"macro_snapshot": ...,
```

Query: select the single most-recent row per indicator, ordered by indicator name:

```sql
SELECT m.indicator, m.date, m.value,
       prev.value AS prev_value
FROM macro m
LEFT JOIN macro prev
  ON prev.indicator = m.indicator
 AND prev.date = (
       SELECT date FROM macro
       WHERE indicator = m.indicator
         AND date < m.date
       ORDER BY date DESC LIMIT 1
     )
WHERE m.date = (
      SELECT date FROM macro AS inner
      WHERE inner.indicator = m.indicator
      ORDER BY date DESC LIMIT 1
    )
ORDER BY m.indicator
```

Each result row as a plain dict. If the table is empty, return `[]`.

**In `_serialize_context`** — insert the `## Macro snapshot` section **after** `## Portfolio positions` and **before** `## Top opportunities`:

```
## Macro snapshot
<one line per indicator: <indicator> | <date> | <value> | chg=<value - prev_value formatted to 3dp, or "n/a">>
(or _none_ if empty)
```

No other changes to `dialogue.py`. Do not touch `_SYSTEM_PROMPT`, `ask()`, `DialogueResult`, or `_build_context`'s other keys.

---

### 3.4 Add `fred:` block to `gatto_farioli/config.yaml`

Append at the end of the file, after the `radar:` block:

```yaml
fred:
  lookback_days: 90
  # Uncomment and edit to override the default 13-series list.
  # series_map is a dict of FRED_ID: "human label".
  # If omitted, the module uses its curated default covering rates,
  # inflation, growth, commodities, dollar, and credit.
  # series_map:
  #   DFF: "Fed Funds Rate"
  #   DGS10: "10Y Treasury"
```

Do not touch any other section of `config.yaml`.

---

### 3.5 Tests — new file `gatto_farioli/tests/test_macro.py`

All tests are **offline** — mock `fredapi.Fred` using `unittest.mock.patch("ingestion.macro.Fred")`. No real FRED API calls. At least 6 tests:

1. **`test_ingest_macro_skips_when_no_api_key`** — ensure `FRED_API_KEY` is absent from `os.environ` (pop it if present); call `ingest_macro({}, db_path)`; assert `result.skipped is True`; assert `result.rows_upserted == 0`; assert `result.skip_reason` contains `"FRED_API_KEY"`.

2. **`test_ingest_macro_upserts_rows`** — set `os.environ["FRED_API_KEY"] = "test"` (restore after test); patch `ingestion.macro.Fred`; configure the mock so `fred.get_series(ANY, observation_start=ANY)` returns a `pd.Series({pd.Timestamp("2026-01-01"): 5.33, pd.Timestamp("2026-01-02"): 5.34})`; call `ingest_macro({"fred": {"series_map": {"DFF": "Fed Funds"}}}, db_path)`; assert `result.rows_upserted == 2`; assert `result.series_succeeded == 1`; query `macro` table and assert both rows exist with `indicator="DFF"`.

3. **`test_ingest_macro_records_failure_and_continues`** — set API key; patch `Fred`; make `get_series` raise `Exception("timeout")` for the first series but return valid data for the second; call `ingest_macro` with two-series config; assert `len(result.failures) == 1`; assert `result.series_succeeded == 1`; assert rows from the second series ARE in the table.

4. **`test_ingest_macro_dry_run_writes_nothing`** — set API key; patch `Fred` to return valid data; call `ingest_macro(config, db_path, dry_run=True)`; assert `macro` table is empty; assert `result.rows_upserted == 0`.

5. **`test_build_context_includes_macro_snapshot`** — insert 2 rows into `macro` table (`indicator='DFF'`, dates `'2026-01-01'` and `'2026-01-02'`, values `5.33` and `5.34`); call `_build_context({"theses": {}}, db_path)`; assert `"macro_snapshot"` key is present; assert `len(ctx["macro_snapshot"]) == 1` (one indicator); assert `ctx["macro_snapshot"][0]["indicator"] == "DFF"`; assert `ctx["macro_snapshot"][0]["value"] == 5.34`.

6. **`test_build_context_macro_snapshot_empty_when_no_rows`** — call `_build_context` on empty DB; assert `ctx["macro_snapshot"] == []`.

Total new tests: ≥ 6. Suite size after Phase J: ≥ 69 (63 existing + 6 new).

---

### 3.6 Verify harness — 27 → 30

Append to `scripts/verify.py` in this order, after check 27:

- **28. `macro ingest skips cleanly when FRED_API_KEY absent`** — temporarily remove `FRED_API_KEY` from `os.environ` (restore in finally); call `ingest_macro({}, db_path)`; assert `result.skipped is True`; assert `result.rows_upserted == 0`; assert `macro` table row count is 0.

- **29. `macro ingest upserts rows with mocked FRED`** — set `os.environ["FRED_API_KEY"] = "verify_test"`; patch `ingestion.macro.Fred` so `get_series` returns `pd.Series({pd.Timestamp("2026-01-10"): 4.5, pd.Timestamp("2026-01-11"): 4.6})`; call `ingest_macro({"fred": {"series_map": {"DGS10": "10Y"}}}, db_path)`; assert `result.rows_upserted == 2`; assert `result.series_succeeded == 1`; assert `conn.execute("SELECT COUNT(*) FROM macro WHERE indicator='DGS10'").fetchone()[0] == 2`.

- **30. `dialogue context macro_snapshot populated from macro table`** — insert one macro row (`indicator='UNRATE'`, `date='2026-01-15'`, `value=4.1`); call `_build_context({"theses": {}}, db_path)`; assert `"macro_snapshot"` in ctx; assert `ctx["macro_snapshot"][0]["indicator"] == "UNRATE"`; assert `ctx["macro_snapshot"][0]["value"] == 4.1`.

Final summary line must read exactly: `Verify: 30/30 passed.`

---

### 3.7 Docs

- **`gatto_farioli/README.md`** — add a Phase J row to the "What works today" table:
  - Capability: "FRED macro ingestion (Phase J)"
  - Module: `ingestion/macro.py`
  - Notes: "13 curated series (rates, inflation, growth, commodities, dollar, credit) via FRED API; skips cleanly if `FRED_API_KEY` unset; enriches `--ask` dialogue context with `## Macro snapshot`"
  - Bump verify count to 30 and append checks 28–30.
  - Add a note under Setup: "`FRED_API_KEY` must be set in `.env` to enable macro ingestion. Free API key at https://fred.stlouisfed.org/docs/api/api_key.html"

- **`README.md`** (root) — bump verify count to 30 and append to the working-on-main list:
  > "FRED macro ingestion per PRODUCT_VISION §3.1 — 13 macro series (rates, inflation, WTI, spreads) stored in `macro` table on every `--ingest` run; dialogue context includes `## Macro snapshot`"

---

## 4. Out of scope — do NOT do any of these

- **No macro signals in opportunity scoring.** `analysis/opportunities.py` does not read from the `macro` table in Phase J. That integration is Phase K.
- **No PortWatch ingestion.** The `portwatch` table exists in the schema but is a separate future phase.
- **No Twitter/Nitter ingestion.** Deferred.
- **No new dependencies.** `fredapi` is already in `requirements.txt`. Do not add `pandas` explicitly (it is a transitive dependency of both `yfinance` and `fredapi`). Do not add any other package.
- **No new tables.** The `macro` table already exists. No schema changes of any kind.
- **No calibration of confidence from outcomes.** Still deferred.
- **No `ACTION_EXECUTE_NOW`.** Still deferred.
- **No alerts wiring.** `analysis/alerts.py` remains a stub.
- **No edits to** `analysis/opportunities.py`, `analysis/outcomes.py`, `analysis/narratives.py`, `analysis/radar.py`, `analysis/brief.py`, `analysis/news_score.py`, `analysis/delta.py`, `analysis/alerts.py`, `analysis/thesis.py`.
- **No edits to** `ingestion/news.py`, `ingestion/prices.py`, `ingestion/kalshi.py`, `ingestion/polymarket.py`.
- **No edits to** `storage/schema.py`, `storage/db.py`, `storage/state.py`, `storage/source_health.py`.
- **No edits to** `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`, `requirements.txt`.
- **No dashboard / Vercel / `api/` / `lib/` / `index.html` / `sw.js` edits.**
- **Do not `git stash pop`, `git stash apply`, or read from `stash@{0}`.** AGENTS.md rule 2.
- **Do not mix this phase with any other work.** AGENTS.md rule 2.

---

## 5. Allowed file list — `git diff --stat` after the commit must show only these paths

1. `gatto_farioli/ingestion/macro.py`
2. `gatto_farioli/run.py`
3. `gatto_farioli/analysis/dialogue.py`
4. `gatto_farioli/config.yaml`
5. `gatto_farioli/scripts/verify.py`
6. `gatto_farioli/tests/test_macro.py` (new file)
7. `gatto_farioli/README.md`
8. `README.md`

If you find yourself wanting to touch any path outside this list, **stop and ask the user**.

Note: the fixup commit (§0 pre-flight) also touches `analysis/dialogue.py` and `config.yaml`. That commit is separate and precedes Phase J. After the fixup commit, `dialogue.py` and `config.yaml` appear again in Phase J's allowed list only for the new macro-related additions.

---

## 6. Definition of done — per AGENTS.md rule 3, run all four and quote their output before committing

```bash
cd gatto_farioli
python -m pytest -q                                           # ≥ 69 passed
python scripts/verify.py                                      # Verify: 30/30 passed.
cd ..
python3 -m gatto_farioli.run --health                         # exits 0; macro row count printed
git status --short                                            # only the 8 allowed paths show as modified
```

Plus these targeted smoke checks:

```bash
# Confirm macro skips cleanly without a real key (FRED_API_KEY unset or empty):
python3 -m gatto_farioli.run --ingest --dry-run
# Expected: "macro: skipped — FRED_API_KEY not set" line in output, no crash.

# Confirm dialogue context now has ## Macro snapshot section:
python3 -m gatto_farioli.run --ask "What are rates doing?" --dry-run
# Expected: "[dry-run] Context: positions=... " — macro_snapshot count visible in context summary.

git diff --stat   # only the 8 allowed paths
```

If any check fails, fix it before committing. Do not weaken assertions or skip tests.

---

## 7. Commit

Single commit. No merge, rebase, or force-push. Title (exact):

```
feat(gatto): add Phase J FRED macro ingestion
```

Body — short bullet list referencing: replaced `ingestion/macro.py` stub with 13-series FRED ingestion; graceful skip when `FRED_API_KEY` absent; `_run_macro` inserted into `run_ingestion` after `prices`; `macro_snapshot` added to dialogue context; verify extended to 30/30; 6 new tests. Append the standard trailer:

```
Co-authored-by: Cursor <cursoragent@cursor.com>
```

---

## 8. Required completion report — per AGENTS.md rule 4

After the commit lands, reply to the user with exactly this structure (no extra preamble):

```
Branch + HEAD:
  main @ <new commit short sha> — "<commit title>"

Files changed:
  <list of paths from git show --stat>

Commands run + results:
  pytest -q                                     → <tail line>
  scripts/verify.py                             → Verify: 30/30 passed.
  python -m gatto_farioli.run --health          → <one-line summary including macro row count>
  python -m gatto_farioli.run --ingest --dry-run → <line showing macro: skipped or macro: ok>
  python -m gatto_farioli.run --ask "What are rates doing?" --dry-run → <context summary line>
  git status --short                            → <output>

Risks:
  <2–4 concise bullets — e.g. FRED API rate limits on free tier; some series (GDPC1) are quarterly and will show large gaps; macro rows grow unbounded without a pruning strategy; fredapi returns a pandas Series and NaN handling must be explicit>

Next step:
  <one sentence proposing Phase K — e.g. "Phase K — macro signal integration: read the macro table in _load_context() inside analysis/opportunities.py and add macro-derived signals (inverted yield curve, elevated HY spreads, WTI momentum) to equity and prediction-market scoring, with the goal of producing the first real POSSIBLE_TRADE candidates.">
```

Do not skip the Risks section. Do not skip the Next step. Both are required by AGENTS.md rule 4.

---

## 9. Known follow-up — not Phase J scope

- **Macro pruning:** the `macro` table will grow without bound. A future phase should add a `RETAIN_DAYS` config knob and prune rows older than the retention window on each ingest.
- **Macro signals in scoring (Phase K):** `_load_context` in `analysis/opportunities.py` does not read the `macro` table. Phase K adds macro-derived signals (yield-curve inversion, HY spread widening, WTI momentum) to the equity and prediction-market scoring so that macro conditions can push candidates from WATCH to INVESTIGATE or POSSIBLE_TRADE.
- **Quarterly series handling:** `GDPC1` (Real GDP) is quarterly. The current implementation fetches and stores it correctly but the opportunity scorer would need to handle the large gaps between observations when using it as a signal.
- **Alerts (Phase L after K):** `analysis/alerts.py` is a stub with `alerts: 0`. Once macro signals exist in scoring, the alerts layer can fire when a thesis-confirming or thesis-breaking macro threshold is crossed.
- **PortWatch ingestion:** the `portwatch` table exists in the schema but has never been populated. Critical for the Hormuz thesis — addressed in a dedicated future phase.
