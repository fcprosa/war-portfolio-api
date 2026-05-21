# Cursor prompt — Phase I: LLM Dialogue CLI (Dialogue Layer foundation)

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

Expected before you start: branch `main`, HEAD `07c6bcba` (Phase H outcome tracking). Working tree clean except untracked `.claude/`, `AGENTS.md`, `PRODUCT_VISION.md`, and the `gatto_farioli/PHASE_*.md` prompt files. Stash entry `wip: future-phase radar narratives kalshi` present.

If the pre-flight does not match, **stop and ask the user before touching any file**.

## 1. Context — read these in full before writing code

- `PRODUCT_VISION.md` — sections 3.2 (Action Classes), 5 (Daily Interaction Model — the exact questions Gatto must answer), 8 (Personality & Behavior — implement every bullet verbatim in the system prompt), 9 (Dialogue Layer system architecture goal), 10.1 (Phase P1 — "Good explanations" is the remaining gap).
- `AGENTS.md` — every rule. One phase = one commit, no `git stash pop`, run the four "before finishing" commands, finish with the required output format.
- `gatto_farioli/analysis/opportunities.py` — read `_Candidate`, the action constants (`NO_EDGE`, `WATCH`, `INVESTIGATE`, `AVOID`, `POSSIBLE_TRADE`), and the `find_opportunities()` public function. Phase I reads from `opportunity_candidates` directly via SQL — do not call `score_opportunities()` from dialogue.
- `gatto_farioli/analysis/outcomes.py` — the `_STATUS_*` constants and the `opportunity_outcomes` table shape (for surfacing track record in context).
- `gatto_farioli/storage/db.py` — the `get_conn`, `query_all`, `query_one` patterns. Mirror these exactly.
- `gatto_farioli/storage/source_health.py` — the `list_unhealthy(db_path)` function signature. Phase I calls it directly.
- `gatto_farioli/storage/schema.py` — read the `positions`, `narrative_clusters`, `briefs`, `opportunity_candidates`, `opportunity_outcomes`, `news` table definitions so you build the correct SQL.
- `gatto_farioli/run.py` — read `run_brief` and `run_radar` to understand the exact shape of a runner that (a) inits the DB, (b) loads config, (c) calls a module function, (d) calls `record_run`, (e) prints to stdout. Mirror that shape exactly for `run_ask`.
- `gatto_farioli/config.yaml` — the `llm:` block: `provider`, `model_analysis`, `model_classification`, `daily_token_budget`, `monthly_cost_cap_usd`. These are the only LLM config keys Phase I reads.
- `gatto_farioli/.env` — do NOT read or log its contents. Know that it must contain `ANTHROPIC_API_KEY`. The `anthropic` SDK reads this variable automatically.
- `gatto_farioli/requirements.txt` — `anthropic` is NOT currently listed. You will add it.
- `gatto_farioli/scripts/verify.py` — the 24-check harness you will extend to 27.

## 2. Goal

Enable Daniel to ask Gatto a question in plain English and receive a structured, evidence-backed answer drawn exclusively from local DB state — no new ingestion, no fabricated data.

From `PRODUCT_VISION.md` §5, these are the exact questions Gatto must handle on day one:

- "Why did X go down today?"
- "What changed in my book risk since yesterday?"
- "What are your top 5 highest-edge bets today?"
- "If I had $N budget, how would you allocate it?"
- "What should I close or reduce now?"
- "What are the best non-war bets this week?"
- "Which Kalshi or Polymarket positions have the biggest mispricing?"
- "What are we early on that consensus still ignores?"

No new tables. No new ingestion. No schema changes. One commit.

The API call is real when `--dry-run` is absent; the test layer mocks it offline.

---

## 3. In scope — exactly these changes, nothing more

### 3.1 New module `gatto_farioli/analysis/dialogue.py`

#### Public surface

```python
from __future__ import annotations

import anthropic
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class DialogueResult:
    question: str
    answer: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    context_summary: str  # e.g. "positions=3 opps=15 narratives=8 news=12 outcomes=2"

def _build_context(config: dict, db_path: Path) -> dict: ...
def _serialize_context(ctx: dict) -> str: ...
def ask(
    question: str,
    config: dict,
    db_path: Path,
    *,
    dry_run: bool = False,
) -> DialogueResult: ...
```

---

#### `_build_context(config, db_path) -> dict`

Reads from existing tables only — no network. Returns a dict with exactly these 9 keys:

| Key | Source | Notes |
|---|---|---|
| `"as_of"` | `datetime.now(timezone.utc).isoformat()` | UTC ISO string |
| `"positions"` | `SELECT * FROM positions` | All rows as plain dicts. Empty list if none. |
| `"top_opportunities"` | `opportunity_candidates` | Top 15 by `score DESC` where `status='open'`. Include: `candidate_key`, `title`, `summary`, `action`, `score`, `confidence`, `source_type`, `related_ticker`, `related_market_ticker`, `catalyst_path`, `invalidation_trigger`, `risk_reward_summary`, `quality_bar_passed`, `quality_bar_missing`, `signals_count`. Empty list if none. |
| `"active_narratives"` | `narrative_clusters` | Where `status IN ('active', 'emerging')`, ordered `article_count DESC`, limited to 8. Each row as plain dict. Empty list if none. |
| `"recent_news"` | `news` | Where `published_at >= (now - 48h)` AND `importance_score >= 5.0`, ordered `importance_score DESC`, limited to 20. Include: `title`, `summary`, `source`, `published_at`, `importance_score`, `sectors`. Empty list if none. |
| `"recent_outcomes"` | `opportunity_outcomes` | Where `resolved_at >= (now - 14d)` AND `resolution_status LIKE 'resolved_%'`, ordered `resolved_at DESC`. All columns. Empty list if none. |
| `"source_health_warnings"` | `storage.source_health.list_unhealthy(db_path)` | List of dicts with `source`, `status`, `failure_count`, `message`. Empty list if none. |
| `"theses"` | `config.get("theses", {})` | The theses dict from config as-is. |
| `"last_radar"` | `briefs` | Most recent row where `type='edge_radar_v1'`, field `content`. Empty string if none. |

Convert each `sqlite3.Row` to `dict` via `dict(row)` before returning. Do not return raw `sqlite3.Row` objects.

---

#### `_serialize_context(ctx: dict) -> str`

Converts the context dict to a compact, token-efficient markdown string injected into the user message. Every section must always be present. Use `_none_` for empty lists and `_no data_` for missing strings — the same convention the radar uses.

Sections in this order:

```
Context as of: <ctx["as_of"]>

## Portfolio positions
<one line per position: ticker | shares=<shares> | avg_cost=<avg_cost> | current_price=<current_price> | unrealized_pnl=<unrealized_pnl>>
(or _none_ if empty)

## Top opportunities (score DESC)
<one line per opportunity: [<action>] score=<score> conf=<confidence> | <candidate_key> | <title>>
  • catalyst: <catalyst_path>        (omit line if null)
  • invalidate if: <invalidation_trigger>   (omit line if null)
  • R/R: <risk_reward_summary>       (omit line if null)
(or _none_ if empty)

## Active narratives
<one line per narrative: [<status>] <title> | articles=<article_count> | momentum_24h=<momentum_24h>>
(or _none_ if empty)

## Recent news (48h, importance ≥ 5)
<one line per article: [<importance_score>] <source> | <title>>
(or _none_ if empty)

## Recent track record
<one line per resolved outcome: <candidate_key> | <action_at_emission> | <resolution_status> | realized=<realized_return>>
(or _no resolved outcomes yet_ if empty)

## Source health warnings
<one line per unhealthy source: <source> | status=<status> | fails=<failure_count> | <message>>
(or _all sources healthy_ if empty)

## Active theses
<for each thesis name + config entry:>
  <thesis_name>: <description first 120 chars>
  confirming: <comma-joined confirming_signals>
  breaking: <comma-joined breaking_signals>
(or _none_ if empty)

## Last radar
<ctx["last_radar"] trimmed to first 3000 characters; append "[truncated]" if longer>
(or _no radar stored yet_ if empty string)
```

Float formatting: round to 2 decimal places. None values: render as `n/a`.

---

#### Module-level constant `_SYSTEM_PROMPT`

Define as a module-level string constant. Encode `PRODUCT_VISION.md` §8 **verbatim** — every bullet:

```
You are Gatto Farioli — a macro and geopolitical market intelligence pilot. Your job is to transform the provided DB context into executable, risk-aware answers. You are not a co-pilot. You are the pilot. Daniel is the final risk authority.

Personality (non-negotiable):
- Decisive, not timid.
- Probabilistic, not dogmatic.
- Concise on tactical questions; do not pad with generic market commentary.
- Brutally honest about uncertainty — say "I don't know" when the data is absent.
- Resistant to narrative hype — require evidence from the context before endorsing a thesis.
- Willing to say "no trade" often. NO_EDGE is a valid and frequent answer.
- Always accountable: state what you know, what you infer, and where you could be wrong.

Action classes (use exactly these strings in Recommendation):
NO_EDGE | WATCH | INVESTIGATE | AVOID | POSSIBLE_TRADE

Every answer must contain exactly these sections with these exact headers:

**Recommendation:** [action class — one of the five above — followed by one sentence]
**Confidence:** [1–10] — [one sentence justifying the level]
**Evidence:** [bullet list of facts cited directly from the context; no fabrication]
**Key risks:** [bullet list]
**Invalidation:** [one sentence — what single event or price level would kill this thesis]
**Execution notes:** [ticker or market, sizing note, timing — or "N/A" if NO_EDGE or AVOID]

Hard rules:
- Never invent data not present in the provided context.
- If the context lacks enough information, say so explicitly and classify as NO_EDGE.
- If asked for a ranked list, rank by score descending and show the action class next to each item.
- Do not use the words "certainly", "definitely", "obviously", or "straightforward".
- Do not add a preamble like "Great question!" or "Sure!".
```

---

#### `ask(question, config, db_path, *, dry_run=False) -> DialogueResult`

Implementation — exact steps:

1. Read the `llm` block from config:
   ```python
   llm_cfg = config.get("llm", {})
   model = llm_cfg.get("model_analysis", "claude-opus-4-6")
   ```
   Use `model_analysis` for all `--ask` calls. Do not use `model_classification` here.

2. Call `_build_context(config, db_path)` → `ctx`.

3. Call `_serialize_context(ctx)` → `serialized`.

4. Build `context_summary`:
   ```python
   context_summary = (
       f"positions={len(ctx['positions'])} "
       f"opps={len(ctx['top_opportunities'])} "
       f"narratives={len(ctx['active_narratives'])} "
       f"news={len(ctx['recent_news'])} "
       f"outcomes={len(ctx['recent_outcomes'])}"
   )
   ```

5. If `dry_run=True`:
   ```python
   return DialogueResult(
       question=question,
       answer="[dry-run: no API call made]",
       model=model,
       prompt_tokens=0,
       completion_tokens=0,
       context_summary=context_summary,
   )
   ```

6. Build the user message:
   ```python
   user_message = f"{serialized}\n\n---\n\nQuestion: {question}"
   ```

7. Call the Anthropic API:
   ```python
   client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env automatically
   try:
       response = client.messages.create(
           model=model,
           max_tokens=2048,
           system=_SYSTEM_PROMPT,
           messages=[{"role": "user", "content": user_message}],
       )
   except anthropic.APIError as exc:
       raise RuntimeError(f"Anthropic API error: {exc}") from exc
   ```

8. Return:
   ```python
   return DialogueResult(
       question=question,
       answer=response.content[0].text,
       model=response.model,
       prompt_tokens=response.usage.input_tokens,
       completion_tokens=response.usage.output_tokens,
       context_summary=context_summary,
   )
   ```

No token budget enforcement in Phase I — usage is surfaced in the `runs` table message only. Budget enforcement is deferred to a future phase.

---

### 3.2 CLI wiring in `gatto_farioli/run.py`

**New argument** — add to `parse_args()`:

```python
parser.add_argument(
    "--ask",
    type=str,
    metavar="QUESTION",
    help="Ask Gatto a question about current portfolio and market state (uses existing DB, no ingestion)",
)
```

**New runner** — add `run_ask` mirroring the exact shape of `run_brief` and `run_radar`:

```python
def run_ask(args: argparse.Namespace) -> int:
    """Ask Gatto a question and print a structured answer."""
    from analysis.dialogue import ask as dialogue_ask
    from config import load_config

    db_path = Path(args.db)
    init_db(db_path)
    config = load_config(args.config)
    started = datetime.now(timezone.utc)
    try:
        result = dialogue_ask(args.ask, config, db_path, dry_run=args.dry_run)
    except RuntimeError as exc:
        record_run("dialogue", "error", str(exc), started, db_path)
        print(f"ERROR: {exc}")
        return 1
    if not args.dry_run:
        msg = (
            f"model={result.model} "
            f"tokens={result.prompt_tokens}+{result.completion_tokens} "
            f"context=[{result.context_summary}]"
        )
        record_run("dialogue", "ok", msg, started, db_path)
    print(result.answer)
    if args.dry_run:
        print(f"\n[dry-run] Context: {result.context_summary}")
    return 0
```

**Wire in `main()`** — add immediately after the `if args.health:` block and before `if args.brief:`:

```python
if args.ask:
    return run_ask(args)
```

`--ask` does NOT run ingestion. It uses the existing DB as-is. `--no-ingest` is implied and the flag is silently ignored when combined with `--ask`.

---

### 3.3 `gatto_farioli/requirements.txt`

Add exactly one line at the end of the file:

```
anthropic>=0.40.0
```

Do not change any other line. Do not re-order.

---

### 3.4 Tests — new file `gatto_farioli/tests/test_dialogue.py`

All tests are **offline** — mock `anthropic.Anthropic` using `unittest.mock.patch`. No real API calls. At least 6 tests:

1. **`test_build_context_returns_required_keys`** — init temp DB with `init_db`; call `_build_context(config, db_path)` where config is a minimal dict `{"theses": {}}`; assert all 9 keys are present: `"as_of"`, `"positions"`, `"top_opportunities"`, `"active_narratives"`, `"recent_news"`, `"recent_outcomes"`, `"source_health_warnings"`, `"theses"`, `"last_radar"`. Assert `ctx["positions"]` is a list and `ctx["top_opportunities"]` is a list.

2. **`test_build_context_positions_populated`** — insert one row into `positions` (`ticker='TST'`, `shares=1.0`, `avg_cost=100.0`, `current_price=110.0`, `market_value=110.0`, `unrealized_pnl=10.0`, `thesis='test'`, `conviction=5`, `last_updated=datetime.now().isoformat()`); call `_build_context`; assert `len(ctx["positions"]) == 1` and `ctx["positions"][0]["ticker"] == "TST"`.

3. **`test_build_context_top_opportunities_ordered_by_score`** — insert 3 rows into `opportunity_candidates` with scores 30, 90, 60 (status `'open'`); call `_build_context`; assert `ctx["top_opportunities"][0]["score"] == 90` and `ctx["top_opportunities"][2]["score"] == 30`.

4. **`test_ask_returns_dialogue_result_with_mocked_api`** — patch `analysis.dialogue.anthropic.Anthropic`; configure mock so `mock_client.return_value.messages.create.return_value` has: `content=[MagicMock(text="Here is my answer")]`, `model="claude-test"`, `usage=MagicMock(input_tokens=100, output_tokens=50)`; call `ask("test question", config, db_path)`; assert `result.answer == "Here is my answer"`, `result.prompt_tokens == 100`, `result.completion_tokens == 50`, `result.model == "claude-test"`.

5. **`test_ask_dry_run_skips_api`** — patch `analysis.dialogue.anthropic.Anthropic`; call `ask("test question", config, db_path, dry_run=True)`; assert `mock_anthropic.return_value.messages.create.call_count == 0`; assert `result.answer == "[dry-run: no API call made]"`; assert `result.prompt_tokens == 0`.

6. **`test_system_prompt_contains_required_sections`** — import `_SYSTEM_PROMPT` from `analysis.dialogue`; assert all of these strings are present: `"NO_EDGE"`, `"POSSIBLE_TRADE"`, `"**Recommendation:**"`, `"**Confidence:**"`, `"**Evidence:**"`, `"**Invalidation:**"`, `"**Execution notes:**"`.

Total new tests: ≥ 6. Suite size after Phase I: ≥ 63 (57 existing + 6 new).

---

### 3.5 Verify harness — 24 → 27

Append to `scripts/verify.py` in this order, after check 24:

- **25. `dialogue context builder returns all 9 required keys`** — init a temp DB; call `_build_context({"theses": {}}, db_path)`; assert all 9 keys are in the returned dict; assert `ctx["positions"]` is a list; assert `ctx["last_radar"]` is a string.

- **26. `dialogue ask dry_run returns result without calling API`** — patch `analysis.dialogue.anthropic.Anthropic` so its constructor is tracked; call `ask("test?", {"theses": {}}, db_path, dry_run=True)`; assert the constructor was NOT called (`mock_anthropic.call_count == 0`); assert `result.answer == "[dry-run: no API call made]"`; assert `result.prompt_tokens == 0`.

- **27. `dialogue ask calls Anthropic API and returns answer`** — patch `analysis.dialogue.anthropic.Anthropic`; configure mock to return a messages response with `content=[MagicMock(text="mocked answer")]`, `model="gatto-test"`, `usage=MagicMock(input_tokens=50, output_tokens=25)`; call `ask("test?", {"theses": {}}, db_path)`; assert `result.answer == "mocked answer"`; assert `result.completion_tokens == 25`; assert `result.model == "gatto-test"`.

Final summary line must read exactly: `Verify: 27/27 passed.`

---

### 3.6 Docs

- **`gatto_farioli/README.md`** — add a Phase I row to the "What works today" table:
  - Capability: "LLM Dialogue CLI (Phase I)"
  - Module: `analysis/dialogue.py`
  - Notes: "ask Gatto any question in plain English; context built from existing DB tables; answer via Claude API per PRODUCT_VISION §5; requires `ANTHROPIC_API_KEY` in `.env`"
  - Bump verify count to 27 and append checks 25–27.
  - Add to "Daily commands":
    ```bash
    # Ask Gatto a question (uses existing DB — run --ingest first for fresh data)
    python run.py --ask "What are your top 5 highest-edge bets right now?"
    python run.py --ask "Why did CF drop today?"
    python run.py --ask "Which Kalshi markets are most mispriced?"

    # Dry-run (prints context summary, no API call)
    python run.py --ask "..." --dry-run
    ```
  - Add a note under Setup: `ANTHROPIC_API_KEY must be set in .env (already in .env.example). `pip install anthropic>=0.40.0` or re-run `pip install -r requirements.txt`.`

- **`README.md`** (root) — bump verify count to 27 and append to the working-on-main list:
  > "LLM Dialogue CLI per PRODUCT_VISION §5 — `python -m gatto_farioli.run --ask 'your question'` queries existing DB state and returns a structured Claude-powered answer with recommendation, confidence, evidence, risks, invalidation, and execution notes"

---

## 4. Out of scope — do NOT do any of these

- **No new tables.** No `token_usage` table, no `dialogue_history` table, no schema changes of any kind.
- **No token budget enforcement.** `daily_token_budget` and `monthly_cost_cap_usd` are in config but Phase I does not enforce them. Usage is surfaced in the `runs` table message only. Enforcement is a future phase.
- **No streaming.** The response is buffered and printed in full. `--ask --stream` is deferred.
- **No conversation history / multi-turn.** Each `--ask` call is stateless. `--chat` mode is deferred.
- **No `ACTION_EXECUTE_NOW`.** Still deferred per PRODUCT_VISION §3.2.
- **No confidence calibration from outcomes.** Deferred from Phase H.
- **No edits to** `analysis/opportunities.py`, `analysis/outcomes.py`, `analysis/narratives.py`, `analysis/radar.py`, `analysis/brief.py`, `analysis/news_score.py`, `analysis/delta.py`, `analysis/alerts.py`, `analysis/thesis.py`.
- **No edits to** `ingestion/*`, `storage/*` (any file), `config.yaml`, `user_state.yaml`, `.env`, `.env.example`, `.gitignore`, `pytest.ini`.
- **No dashboard / Vercel / `api/` / `lib/` / `index.html` / `sw.js` edits.**
- **Do not `git stash pop`, `git stash apply`, or read from `stash@{0}`.** AGENTS.md rule 2.
- **Do not mix this phase with any other work.** AGENTS.md rule 2.

---

## 5. Allowed file list — `git diff --stat` after the commit must show only these paths

1. `gatto_farioli/analysis/dialogue.py` (new file)
2. `gatto_farioli/run.py`
3. `gatto_farioli/requirements.txt`
4. `gatto_farioli/scripts/verify.py`
5. `gatto_farioli/tests/test_dialogue.py` (new file)
6. `gatto_farioli/README.md`
7. `README.md`

If you find yourself wanting to touch any path outside this list, **stop and ask the user**.

---

## 6. Definition of done — per AGENTS.md rule 3, run all four and quote their output before committing

```bash
cd gatto_farioli
python -m pytest -q                                                    # ≥ 63 passed
python scripts/verify.py                                               # Verify: 27/27 passed.
cd ..
python3 -m gatto_farioli.run --health                                  # exits 0, no tracebacks
git status --short                                                     # only the 7 allowed paths
```

Plus these targeted smoke checks:

```bash
python3 -m gatto_farioli.run --ask "What are your top 5 highest-edge bets right now?" --dry-run
# Expected: prints "[dry-run: no API call made]" followed by "[dry-run] Context: positions=..."

git diff --stat   # only the 7 allowed paths
```

If any check fails, fix it before committing. Do not weaken assertions or skip tests.

---

## 7. Commit

Single commit. No merge, rebase, or force-push. Title (exact):

```
feat(gatto): add Phase I LLM dialogue CLI — ask Gatto anything
```

Body — short bullet list referencing: new `analysis/dialogue.py` with `_build_context` / `_serialize_context` / `ask()`; `--ask QUESTION` CLI flag in `run.py`; `anthropic>=0.40.0` added to `requirements.txt`; 6 new tests in `test_dialogue.py`; verify extended to 27/27. Append the standard trailer:

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
  scripts/verify.py                             → Verify: 27/27 passed.
  python -m gatto_farioli.run --health          → <one-line summary>
  python -m gatto_farioli.run --ask "..." --dry-run → <output showing context summary>
  git status --short                            → <output>

Risks:
  <2–4 concise bullets — e.g. context window overflow on large DBs; anthropic SDK version pin risk; ANTHROPIC_API_KEY absent causes clean RuntimeError but no fallback; serializer truncates last_radar at 3000 chars which may drop key signals for long radars>

Next step:
  <one sentence — e.g. "Phase J — token budget enforcement: persist per-call token usage to a new token_usage table and raise BudgetExceeded before the API call when the rolling daily total would exceed daily_token_budget from config.">
```

Do not skip the Risks section. Do not skip the Next step. Both are required by AGENTS.md rule 4.

---

## 9. Known follow-up — not Phase I scope

- **Token budget enforcement (Phase J):** read `daily_token_budget` from config, persist a `token_usage` table (`date`, `module`, `prompt_tokens`, `completion_tokens`, `cost_usd_estimate`), raise `BudgetExceeded` before the API call if the rolling daily total would be exceeded. `monthly_cost_cap_usd` enforcement is a further step.
- **Streaming output:** `--ask --stream` for real-time token streaming via `client.messages.stream()`. Deferred.
- **Multi-turn conversation:** `--chat` mode with a conversation history buffer. Deferred.
- **Confidence calibration from outcomes:** use Phase H hit-rate data to adjust `confidence` in `opportunities.py`. Deferred from Phase H.
- **`ACTION_EXECUTE_NOW`:** PRODUCT_VISION §3.2. Still deferred until extra safeguards are in place.
