# Cursor prompt — Phase K: Macro Signal Integration into Opportunity Scoring

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

Expected before you start: branch `main`, HEAD `9aa1c21` (Phase J FRED macro ingestion). Working tree clean except untracked `.claude/`, `AGENTS.md`, `CLAUDE.md`, `PRODUCT_VISION.md`, and the `gatto_farioli/PHASE_*.md` prompt files. Stash entry `wip: future-phase radar narratives kalshi` present.

If the pre-flight does not match, **stop and ask the user before touching any file**.

## 1. Context — read these in full before writing code

- `PRODUCT_VISION.md` — sections 3.1 step 2 ("Cluster narratives"), step 4 ("Estimate probabilities"), step 5 ("Find mispricings"), 7 (Quality Bar — evidence from stored data), 10.1 (Phase P1 goal: more high-conviction actionable ideas), 11 (success metric: more POSSIBLE_TRADE rows).
- `AGENTS.md` — every rule. One phase = one commit, no `git stash pop`, run the four "before finishing" commands, finish with the required output format.
- `gatto_farioli/analysis/opportunities.py` — read the **entire file** before writing code. Specifically:
  - All `SIGNAL_*` and `SOURCE_*` constants (lines 43–59) — you will add `SIGNAL_MACRO = "macro"` alongside them.
  - `_has_critical_missing`, `_source_type_from_signals`, `_finalize_action` — understand every gate. Phase K must not weaken any existing gate.
  - `_load_context` — read its full body and its return dict keys. You will add two new keys: `"macro"` and `"macro_cfg"`.
  - `_score_equity_candidate` — read the full function. You will insert the macro signal layer **after** the base confidence computation and **before** the action assignment block.
  - `_score_kalshi_candidate` — same insertion point.
  - `_build_candidates` — unchanged; it calls `_load_context` and passes `ctx` to the scoring functions. No changes needed here.
  - `__all__` at the bottom — you will add `"SIGNAL_MACRO"` to it.
- `gatto_farioli/storage/schema.py` — read the `macro` table definition: `(indicator TEXT, date DATE, value REAL, PRIMARY KEY (indicator, date))`. Phase K reads from this table in `_load_context`. No schema changes.
- `gatto_farioli/storage/db.py` — `get_conn`, `query_all`. Mirror the existing patterns.
- `gatto_farioli/tests/test_opportunities.py` — read all existing tests. The new tests must not conflict with them. Macro signals are absent from existing test fixtures; add macro rows only in the new tests.
- `gatto_farioli/scripts/verify.py` — the 30-check harness you will extend to 33.
- `gatto_farioli/config.yaml` — you will append an optional `macro_signals:` block after the `fred:` block.

## 2. Goal

Make macro data drive scoring. Phase J filled the `macro` table; Phase K reads it inside `_load_context`, derives deterministic signals (`wti_momentum_bullish`, `inflation_breakeven_elevated`, `yield_curve_inverted`, etc.), and applies a bounded score + confidence boost so candidates with strong macro tailwinds can cross the `POSSIBLE_TRADE` threshold.

The current state: 0 `POSSIBLE_TRADE` candidates, 31 `INVESTIGATE`, 167 `WATCH`. Phase K gives Gatto the mechanism to produce its first `POSSIBLE_TRADE` when macro conditions support it.

No new tables. No schema changes. No new dependencies. One commit.

If the `macro` table is empty (e.g. `FRED_API_KEY` not yet set), Phase K must be a strict no-op — every existing score, confidence, and action is unchanged. Backward compatibility is non-negotiable.

---

## 3. In scope — exactly these changes, nothing more

### 3.1 New constants in `gatto_farioli/analysis/opportunities.py`

Add **immediately after** the existing `SIGNAL_SOURCE_HEALTH = "source_health"` line:

```python
SIGNAL_MACRO = "macro"
```

Add the following module-level constants **immediately after** `FLAT_MOVE_PCT = 1.5`:

```python
# ── Macro signal layer constants (Phase K) ─────────────────────────────────
_DEFAULT_MACRO_SIGNALS_CFG: dict[str, float] = {
    "wti_momentum_abs": 2.0,          # |DCOILWTICO change| > this (USD) → WTI momentum signal
    "inflation_breakeven_floor": 2.5,  # T5YIE value > this (%) → elevated inflation signal
    "hy_spread_elevated": 4.5,         # BAMLH0A0HYM2 value > this (%) → risk-off signal
    "yield_curve_inversion": 0.0,      # T10Y2Y value < this (%) → inverted yield curve signal
}
_MACRO_SCORE_BOOST_PER_SIGNAL = 5.0   # score added per triggered macro signal
_MACRO_SCORE_BOOST_MAX = 15.0         # hard ceiling on total macro score boost
_MACRO_CONFIDENCE_BOOST_PER_SIGNAL = 0.5   # confidence added per triggered macro signal
_MACRO_CONFIDENCE_BOOST_MAX = 1.5          # hard ceiling on total macro confidence boost
_MACRO_EVIDENCE_KEYS = ("DCOILWTICO", "DCOILBRENTEU", "T10Y2Y", "BAMLH0A0HYM2", "T5YIE", "DFF", "DGS10")
```

---

### 3.2 New helper `_get_macro_cfg(config)`

Add **immediately before** `_has_critical_missing`:

```python
def _get_macro_cfg(config: dict[str, Any]) -> dict[str, float]:
    """Merge user-supplied macro_signals config with built-in defaults.

    All keys are optional in config; missing keys fall back to _DEFAULT_MACRO_SIGNALS_CFG.
    """
    user = config.get("macro_signals") or {}
    return {**_DEFAULT_MACRO_SIGNALS_CFG, **{k: float(v) for k, v in user.items()}}
```

---

### 3.3 New helper `_macro_signals_for_ticker(groups, macro, macro_cfg)`

Add **immediately after** `_get_macro_cfg`:

```python
def _macro_signals_for_ticker(
    groups: set[str],
    macro: dict[str, dict[str, Any]],
    macro_cfg: dict[str, float],
) -> list[str]:
    """Return triggered macro signal tags for an equity candidate.

    ``macro`` is a dict of indicator → {"value": float|None, "change": float|None}.
    Returns an empty list when ``macro`` is empty (FRED key not set) or when no
    threshold is crossed — scoring is unchanged in that case.
    """
    if not macro:
        return []

    triggered: list[str] = []

    is_oil = bool(groups & {"oil", "oil_tankers"})
    is_fertilizer = "fertilizer" in groups
    is_gold = "gold" in groups
    is_defense = any("defense" in g for g in groups)

    wti_thresh = macro_cfg.get("wti_momentum_abs", _DEFAULT_MACRO_SIGNALS_CFG["wti_momentum_abs"])
    inf_floor = macro_cfg.get("inflation_breakeven_floor", _DEFAULT_MACRO_SIGNALS_CFG["inflation_breakeven_floor"])
    hy_thresh = macro_cfg.get("hy_spread_elevated", _DEFAULT_MACRO_SIGNALS_CFG["hy_spread_elevated"])
    yc_thresh = macro_cfg.get("yield_curve_inversion", _DEFAULT_MACRO_SIGNALS_CFG["yield_curve_inversion"])

    # WTI momentum — relevant for oil, tankers, fertilizer
    wti_change = (macro.get("DCOILWTICO") or {}).get("change")
    if wti_change is not None and (is_oil or is_fertilizer):
        if wti_change > wti_thresh:
            triggered.append("wti_momentum_bullish")
        elif wti_change < -wti_thresh:
            triggered.append("wti_momentum_bearish")

    # Inflation breakeven — relevant for fertilizer (cost-push), gold (inflation hedge)
    t5yie_val = (macro.get("T5YIE") or {}).get("value")
    if t5yie_val is not None and t5yie_val > inf_floor and (is_fertilizer or is_gold):
        triggered.append("inflation_breakeven_elevated")

    # Yield curve inversion — macro risk-off; tailwind for gold and defense
    yc_val = (macro.get("T10Y2Y") or {}).get("value")
    if yc_val is not None and yc_val < yc_thresh:
        triggered.append("yield_curve_inverted")
        if is_gold or is_defense:
            triggered.append("risk_off_tailwind")

    # HY credit spread widening — broad risk-off; tailwind for gold and defense
    hy_val = (macro.get("BAMLH0A0HYM2") or {}).get("value")
    if hy_val is not None and hy_val > hy_thresh:
        triggered.append("hy_spread_elevated")
        if is_gold or is_defense:
            triggered.append("risk_off_tailwind")

    # Deduplicate while preserving order
    return list(dict.fromkeys(triggered))
```

---

### 3.4 New helper `_macro_signals_for_category(category, macro, macro_cfg)`

Add **immediately after** `_macro_signals_for_ticker`:

```python
def _macro_signals_for_category(
    category: str,
    macro: dict[str, dict[str, Any]],
    macro_cfg: dict[str, float],
) -> list[str]:
    """Return triggered macro signal tags for a Kalshi/Polymarket candidate.

    Keyed on the market's category string rather than watchlist groups.
    Returns an empty list when ``macro`` is empty.
    """
    if not macro:
        return []

    triggered: list[str] = []
    cat = (category or "").lower()

    wti_thresh = macro_cfg.get("wti_momentum_abs", _DEFAULT_MACRO_SIGNALS_CFG["wti_momentum_abs"])
    hy_thresh = macro_cfg.get("hy_spread_elevated", _DEFAULT_MACRO_SIGNALS_CFG["hy_spread_elevated"])
    yc_thresh = macro_cfg.get("yield_curve_inversion", _DEFAULT_MACRO_SIGNALS_CFG["yield_curve_inversion"])

    is_energy = cat in {"energy", "commodities"}
    is_rates = cat in {"rates", "macro", "inflation", "economics"}
    is_geo = cat in {"geopolitics"}

    # WTI momentum → energy / commodity markets
    wti_change = (macro.get("DCOILWTICO") or {}).get("change")
    if wti_change is not None and is_energy:
        if wti_change > wti_thresh:
            triggered.append("wti_momentum_bullish")
        elif wti_change < -wti_thresh:
            triggered.append("wti_momentum_bearish")

    # Yield curve → rates and macro markets
    yc_val = (macro.get("T10Y2Y") or {}).get("value")
    if yc_val is not None and yc_val < yc_thresh and is_rates:
        triggered.append("yield_curve_inverted")

    # Fed Funds rate movement → rates markets
    dff_change = (macro.get("DFF") or {}).get("change")
    if dff_change is not None and abs(dff_change) > 0.05 and is_rates:
        triggered.append("fed_funds_moving")

    # HY spread → geopolitics (risk proxy)
    hy_val = (macro.get("BAMLH0A0HYM2") or {}).get("value")
    if hy_val is not None and hy_val > hy_thresh and is_geo:
        triggered.append("hy_spread_elevated")

    return list(dict.fromkeys(triggered))
```

---

### 3.5 Extend `_load_context` to read macro snapshot

Inside `_load_context`, **within the existing `with get_conn(db_path) as conn:` block**, add the following query **after** the `unhealthy` query and **before** the closing of the `with` block:

```python
        # Macro snapshot — latest value + previous observation per indicator (Phase K)
        macro_rows = conn.execute(
            """
            SELECT m.indicator,
                   m.value,
                   prev.value AS prev_value
            FROM macro m
            LEFT JOIN macro prev
              ON prev.indicator = m.indicator
             AND prev.date = (
                   SELECT date FROM macro
                   WHERE indicator = m.indicator
                     AND date < m.date
                   ORDER BY date DESC
                   LIMIT 1
                 )
            WHERE m.date = (
                  SELECT date FROM macro AS inner
                  WHERE inner.indicator = m.indicator
                  ORDER BY date DESC
                  LIMIT 1
                )
            """
        ).fetchall()
        macro: dict[str, dict[str, Any]] = {}
        for row in macro_rows:
            v = row["value"]
            pv = row["prev_value"]
            change: float | None = None
            if v is not None and pv is not None:
                change = float(v) - float(pv)
            macro[row["indicator"]] = {
                "value": float(v) if v is not None else None,
                "change": change,
            }
```

Then extend the `return` dict at the end of `_load_context` to include two new keys:

```python
    return {
        "narratives": narratives,
        "news": news_rows,
        "universe": universe,
        "prices": prices,
        "ticker_to_groups": ticker_to_groups,
        "unhealthy": unhealthy,
        "macro": macro,                       # new in Phase K
        "macro_cfg": _get_macro_cfg(config),  # new in Phase K
    }
```

Do not change any other part of `_load_context`.

---

### 3.6 Insert macro signal layer into `_score_equity_candidate`

In `_score_equity_candidate`, locate the block that computes `confidence` and applies the `no_live_price` penalty. It currently ends with:

```python
    if "no_live_price" in missing:
        confidence = max(1.0, confidence - 3.0)
```

**Immediately after** that line, insert the macro signal layer block:

```python
    # ── Macro signal layer (Phase K) ──────────────────────────────────────
    _macro_triggered = _macro_signals_for_ticker(
        groups, ctx.get("macro", {}), ctx.get("macro_cfg", {})
    )
    if _macro_triggered:
        signal_types.add(SIGNAL_MACRO)
        signals.append(SIGNAL_MACRO)
        evidence["macro"] = {
            "signals": _macro_triggered,
            "snapshot": {
                k: ctx["macro"][k]
                for k in _MACRO_EVIDENCE_KEYS
                if k in ctx.get("macro", {})
            },
        }
        _score_boost = min(
            _MACRO_SCORE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_SCORE_BOOST_PER_SIGNAL,
        )
        _conf_boost = min(
            _MACRO_CONFIDENCE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_CONFIDENCE_BOOST_PER_SIGNAL,
        )
        score = min(100.0, score + _score_boost)
        confidence = min(10.0, confidence + _conf_boost)
    # ──────────────────────────────────────────────────────────────────────
```

The action assignment block (`if narrative_only: ... elif ...`) comes **after** this insertion and will therefore use the macro-boosted `score` and `confidence`. Do not move any other code.

---

### 3.7 Insert macro signal layer into `_score_kalshi_candidate`

In `_score_kalshi_candidate`, locate the block that computes `confidence` and applies the `no_market_odds` penalty:

```python
    confidence = min(10.0, 2.0 + len(signal_types) * 1.8)
    if not has_odds:
        confidence = max(1.0, confidence - 3.0)
```

**Immediately after** that block, insert:

```python
    # ── Macro signal layer (Phase K) ──────────────────────────────────────
    _macro_triggered = _macro_signals_for_category(
        category, ctx.get("macro", {}), ctx.get("macro_cfg", {})
    )
    if _macro_triggered:
        signal_types.add(SIGNAL_MACRO)
        signals.append(SIGNAL_MACRO)
        evidence["macro"] = {
            "signals": _macro_triggered,
            "snapshot": {
                k: ctx["macro"][k]
                for k in _MACRO_EVIDENCE_KEYS
                if k in ctx.get("macro", {})
            },
        }
        _score_boost = min(
            _MACRO_SCORE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_SCORE_BOOST_PER_SIGNAL,
        )
        _conf_boost = min(
            _MACRO_CONFIDENCE_BOOST_MAX,
            len(_macro_triggered) * _MACRO_CONFIDENCE_BOOST_PER_SIGNAL,
        )
        score = min(100.0, score + _score_boost)
        confidence = min(10.0, confidence + _conf_boost)
    # ──────────────────────────────────────────────────────────────────────
```

The action assignment block (`if politics_only or narrative_only: ...`) comes **after** this insertion.

---

### 3.8 Update `__all__`

Add `"SIGNAL_MACRO"` to the `__all__` list at the bottom of `analysis/opportunities.py`, alongside the existing `SIGNAL_*` exports. The existing exported names must not change.

---

### 3.9 Add `macro_signals:` block to `gatto_farioli/config.yaml`

Append **at the very end of the file**, after the `fred:` block:

```yaml
macro_signals:
  # Thresholds used by the scoring layer to fire macro signals.
  # All keys are optional — the module uses the defaults below if omitted.
  wti_momentum_abs: 2.0           # |DCOILWTICO change| in USD
  inflation_breakeven_floor: 2.5  # T5YIE value in %
  hy_spread_elevated: 4.5         # BAMLH0A0HYM2 value in %
  yield_curve_inversion: 0.0      # T10Y2Y value in % (< this → inverted)
```

Do not change any other section of `config.yaml`.

---

### 3.10 Tests — extend `gatto_farioli/tests/test_opportunities.py`

Add at least 5 new tests. All offline — use a temp SQLite DB seeded with fixture data. No network, no FRED API.

1. **`test_macro_signals_for_ticker_empty_macro_returns_empty`** — call `_macro_signals_for_ticker({"oil"}, {}, {})` with an empty macro dict; assert the result is `[]`.

2. **`test_macro_signals_for_ticker_wti_bullish`** — call `_macro_signals_for_ticker({"oil"}, {"DCOILWTICO": {"value": 82.0, "change": 3.5}}, _DEFAULT_MACRO_SIGNALS_CFG)`; assert `"wti_momentum_bullish"` is in the result.

3. **`test_macro_signals_for_ticker_fertilizer_inflation`** — call `_macro_signals_for_ticker({"fertilizer"}, {"T5YIE": {"value": 2.8, "change": 0.1}, "DCOILWTICO": {"value": 80.0, "change": 0.5}}, _DEFAULT_MACRO_SIGNALS_CFG)`; assert `"inflation_breakeven_elevated"` is in the result; assert `"wti_momentum_bullish"` is NOT in the result (change=0.5 < threshold=2.0).

4. **`test_macro_signals_empty_when_macro_table_empty`** — init a temp DB (no macro rows); insert a full equity candidate setup (narrative, news, price rows for ticker `"CF"`); call `score_opportunities(config, db_path)`; assert `result.candidates_scored >= 1`; load the `CF` candidate from `opportunity_candidates`; assert `"macro"` is NOT in the JSON-decoded `evidence` field.

5. **`test_equity_candidate_score_boosted_by_macro_signals`** — init a temp DB; insert macro rows for `DCOILWTICO` with latest value `82.0` and previous `79.0` (change = `+3.0`, above threshold `2.0`); insert a narrative cluster with sector `oil`, an `oil_tankers` ticker `"FRO"` in config watchlist, a `prices` row for `FRO`, and a `news` row with sector `oil`; call `score_opportunities`; load the `FRO` candidate; assert `evidence["macro"]["signals"]` contains `"wti_momentum_bullish"`; assert `score >= 5.0` higher than the score of the same candidate with an empty macro table (test both in sequence using two temp DBs or reset the macro table between runs).

Total new tests: ≥ 5. Suite size after Phase K: ≥ 74 (69 existing + 5 new).

---

### 3.11 Verify harness — 30 → 33

Append to `scripts/verify.py` in this order, after check 30:

- **31. `macro_signals_for_ticker returns wti_momentum_bullish for oil group`** — import `_macro_signals_for_ticker`, `_DEFAULT_MACRO_SIGNALS_CFG` from `analysis.opportunities`; call `_macro_signals_for_ticker({"oil"}, {"DCOILWTICO": {"value": 85.0, "change": 3.5}}, _DEFAULT_MACRO_SIGNALS_CFG)`; assert `"wti_momentum_bullish"` in result; assert result is non-empty.

- **32. `macro_signals_for_ticker returns empty list when macro dict empty`** — call `_macro_signals_for_ticker({"oil", "fertilizer"}, {}, _DEFAULT_MACRO_SIGNALS_CFG)`; assert `result == []`. Then call `_macro_signals_for_category("energy", {}, _DEFAULT_MACRO_SIGNALS_CFG)`; assert `result == []`.

- **33. `equity candidate evidence includes macro key when macro rows seeded`** — init temp DB; insert one macro row: `(indicator='DCOILWTICO', date='2026-01-01', value=82.0)` and one prior row `(indicator='DCOILWTICO', date='2025-12-31', value=78.5)` so change = `3.5` > threshold; insert a config with watchlist `{"oil_tankers": ["TST"]}`, a `prices` row for `TST`, and a `narrative_clusters` row with sector `oil` and `related_tickers='["TST"]'`; call `score_opportunities(config, db_path)`; load the `TST` candidate from `opportunity_candidates`; assert `"macro"` in `json.loads(evidence)`; assert `"wti_momentum_bullish"` in `json.loads(evidence)["macro"]["signals"]`.

Final summary line must read exactly: `Verify: 33/33 passed.`

---

### 3.12 Docs

- **`gatto_farioli/README.md`** — add a Phase K row to the "What works today" table:
  - Capability: "Macro signal integration (Phase K)"
  - Module: `analysis/opportunities.py`
  - Notes: "reads `macro` table in `_load_context`; triggers `wti_momentum_bullish/bearish`, `inflation_breakeven_elevated`, `yield_curve_inverted`, `risk_off_tailwind`, `hy_spread_elevated`, `fed_funds_moving`; bounded score (+5/signal, max +15) and confidence (+0.5/signal, max +1.5) boost; no-op when macro table is empty"
  - Bump verify count to 33 and append checks 31–33.

- **`README.md`** (root) — bump verify count to 33 and append to the working-on-main list:
  > "Macro signal integration per PRODUCT_VISION §3.1 — FRED macro snapshot drives deterministic score/confidence boosts in opportunity scoring; WTI momentum, inflation breakeven, yield curve, and HY spread signals fire against watchlist groups and Kalshi categories"

---

## 4. Out of scope — do NOT do any of these

- **No changes to `_finalize_action` gates.** The `POSSIBLE_TRADE` gates (`score >= 75`, `confidence >= 7`, `signals_count >= 3`, `has_tradable_instrument`, `diverse`) are **not weakened**. Macro signals help candidates reach the gates naturally — the gates themselves stay.
- **No new action constants.** `ACTION_EXECUTE_NOW` is still deferred.
- **No changes to `upsert_opportunity_candidates`.** The new `evidence["macro"]` sub-key is stored inside the existing `evidence` JSON column without any schema changes.
- **No changes to `compute_quality_bar`.** Phase K does not affect the Quality Bar; macro signals are a scoring layer, not a quality gate.
- **No changes to `_finalize_action`**, `_build_candidates`, `_score_equity_candidate` (beyond the precise insertion in §3.6), or `_score_kalshi_candidate` (beyond §3.7).
- **No new ingestion.** No PortWatch, no Twitter, no alerts.
- **No edits to** `ingestion/*`, `analysis/outcomes.py`, `analysis/narratives.py`, `analysis/radar.py`, `analysis/brief.py`, `analysis/news_score.py`, `analysis/delta.py`, `analysis/alerts.py`, `analysis/thesis.py`, `analysis/dialogue.py`.
- **No edits to** `storage/*`, `run.py`, `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`, `requirements.txt`.
- **No dashboard / Vercel / `api/` / `lib/` / `index.html` / `sw.js` edits.**
- **Do not `git stash pop`, `git stash apply`, or read from `stash@{0}`.** AGENTS.md rule 2.
- **Do not mix this phase with any other work.** AGENTS.md rule 2.

---

## 5. Allowed file list — `git diff --stat` after the commit must show only these paths

1. `gatto_farioli/analysis/opportunities.py`
2. `gatto_farioli/config.yaml`
3. `gatto_farioli/scripts/verify.py`
4. `gatto_farioli/tests/test_opportunities.py`
5. `gatto_farioli/README.md`
6. `README.md`

If you find yourself wanting to touch any path outside this list, **stop and ask the user**.

---

## 6. Definition of done — per AGENTS.md rule 3, run all four and quote their output before committing

```bash
cd gatto_farioli
python -m pytest -q                                           # ≥ 74 passed
python scripts/verify.py                                      # Verify: 33/33 passed.
cd ..
python3 -m gatto_farioli.run --health                         # exits 0, no tracebacks
git status --short                                            # only the 6 allowed paths show as modified
```

Plus these targeted checks:

```bash
# Confirm backward compat — empty macro table must leave scores unchanged:
python3 -m gatto_farioli.run --radar --no-ingest
# Expected: radar renders without error; no "macro" keys in evidence unless macro table has rows.

# Confirm SIGNAL_MACRO is exported:
python3 -c "from gatto_farioli.analysis.opportunities import SIGNAL_MACRO; print(SIGNAL_MACRO)"
# Expected: "macro"

git diff --stat   # only the 6 allowed paths
```

If any check fails, fix it before committing. Do not weaken any existing assertion or gate.

---

## 7. Commit

Single commit. No merge, rebase, or force-push. Title (exact):

```
feat(gatto): add Phase K macro signal integration into opportunity scoring
```

Body — short bullet list referencing: new `SIGNAL_MACRO` constant; `_get_macro_cfg`, `_macro_signals_for_ticker`, `_macro_signals_for_category` helpers; macro snapshot loaded in `_load_context`; bounded score (+5/signal max +15) and confidence (+0.5/signal max +1.5) boost inserted in both equity and Kalshi scoring functions; no-op when macro table is empty; verify extended to 33/33; 5 new tests. Append the standard trailer:

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
  pytest -q                            → <tail line>
  scripts/verify.py                    → Verify: 33/33 passed.
  python -m gatto_farioli.run --health → <one-line summary>
  python -m gatto_farioli.run --opportunities (or --ingest --dry-run showing action distribution) → <action counts>
  git status --short                   → <output>

Risks:
  <2–4 concise bullets — e.g. macro boost is additive and could push borderline candidates over POSSIBLE_TRADE when macro data is stale; WTI change is absolute not percentage so a $2 move on $40 oil vs $120 oil has different significance; risk_off_tailwind deduplication depends on dict.fromkeys ordering; the macro table must have two rows per indicator for change to be non-None>

Next step:
  <one sentence — e.g. "Phase L — Alerts system: implement generate_alerts() in analysis/alerts.py to fire structured alert rows into the alerts table when a POSSIBLE_TRADE candidate appears, a thesis-breaking signal threshold is crossed, or a narrative cluster crosses from emerging to active — wiring proactive signal delivery for PRODUCT_VISION §3.1 step 8.">
```

Do not skip the Risks section. Do not skip the Next step. Both are required by AGENTS.md rule 4.

---

## 9. Known follow-up — not Phase K scope

- **Percentage-based WTI threshold:** the current threshold is absolute dollars (`wti_momentum_abs`). A future tweak should offer `wti_momentum_pct` (e.g. 3%) as an alternative so the signal is price-level-agnostic.
- **Macro staleness check:** if the most recent `macro` row is older than `staleness_hours` (from `radar:` config), the signal should be flagged as stale rather than silently used. This is a one-liner quality guard for a future patch.
- **Alerts (Phase L):** `analysis/alerts.py` is still a stub with `alerts: 0`. Once POSSIBLE_TRADE candidates begin appearing (as Phase K enables), Phase L fires alert rows into the `alerts` table when a candidate crosses that threshold or a thesis-breaking macro threshold is hit.
- **Outcome calibration (Phase M after L):** once Gatto begins producing POSSIBLE_TRADE rows and the outcome tracker resolves them, the Phase H measurement infrastructure can feed back into confidence via a calibration multiplier — closing the learning loop deferred since Phase H.
- **PortWatch ingestion:** the `portwatch` table exists but has never been populated. The Hormuz thesis explicitly depends on `portwatch_7dma` signals. This is a focused future phase that adds a PortWatch HTTP fetch to the ingestion pipeline.
