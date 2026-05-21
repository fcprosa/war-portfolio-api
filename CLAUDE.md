# CLAUDE.md

## Project

**War Portfolio API + Gatto Farioli** — a macro/geopolitical trading workspace for Daniel.

1. **War Portfolio** — browser dashboard (`index.html`) + Vercel serverless APIs for live portfolio cards, scanner, news, Kalshi/Polymarket quotes, PortWatch, and Claude prompt handoff. State lives in **Vercel Blob** (not this repo’s SQLite).
2. **Gatto Farioli** — local-first **Python** engine under `gatto_farioli/` that ingests RSS, prices, prediction markets, and thesis config into **`argos.db` (SQLite)**, scores opportunities, emits deterministic briefs/radar, tracks outcomes, and answers questions via `--ask` (Anthropic).

The two systems share a repo; Gatto is meant to complement the dashboard over time. Product direction: `PRODUCT_VISION.md`. Agent workflow rules: `AGENTS.md`.

---

## Stack

| Area | Technology |
|---|---|
| Dashboard + API | Vanilla **JavaScript** (ES modules), **no TypeScript**, no React/Next |
| API hosting | **Vercel** serverless (`api/*.js`) |
| Dashboard state | **Vercel Blob** + optional **Supabase** (memory scripts) |
| Intelligence engine | **Python 3** + **SQLite** (`gatto_farioli/argos.db`, gitignored) |
| Config | `gatto_farioli/config.yaml` (portfolio, theses, sources, schedule, LLM) |
| LLM (Gatto `--ask` only) | **Anthropic** via `anthropic` SDK; key in `gatto_farioli/.env` |

There is **no** repo-wide TypeScript or `tsc`. Do not assume Next.js patterns.

---

## Commands

### Gatto Farioli (primary backend work — run from `gatto_farioli/` with `.venv` active)

| Task | Command |
|---|---|
| Setup | `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| Default ingest | `python run.py` or `python run.py --ingest` |
| Health | `python run.py --health` |
| Brief | `python run.py --brief` |
| Radar | `python run.py --radar` |
| Outcomes | `python run.py --outcomes` |
| Ask (LLM) | `python run.py --ask "your question"` |
| Dry-run (no DB writes) | `python run.py --ingest --dry-run` |
| Test all | `pytest` or `python -m pytest -q` |
| Test single | `pytest tests/test_dialogue.py -q` |
| Verify harness | `python scripts/verify.py` → must end with `Verify: 27/27 passed.` |

From **repo root** (equivalent):

```bash
python3 -m gatto_farioli.run --health
cd gatto_farioli && pytest -q && python scripts/verify.py
```

### War Portfolio (dashboard / Vercel — repo root)

| Task | Command |
|---|---|
| Memory seed | `npm run seed:memory` |
| Memory verify | `npm run verify:memory` |
| Prompt dry-run | `npm run dry:prompt` |

No `npm run build`, `lint`, or `typecheck` in this repo.

**IMPORTANT — before finishing any Gatto Farioli task**, always run:

```bash
cd gatto_farioli && pytest -q && python scripts/verify.py && cd .. && python3 -m gatto_farioli.run --health
git status --short
```

---

## Architecture Map

```text
war-portfolio-api/
├── index.html, sw.js     → War Portfolio browser UI (do not change unless tasked)
├── api/                  → Vercel serverless routes (state, brief, news, quote, kalshi, polymarket, portwatch, scan, agent, …)
├── lib/                  → Shared JS: state helpers, news, kalshi, polymarket, scanner, memory, agent
├── scripts/              → Node maintenance (seed_memory, verify_memory, dry_prompt, schema.sql)
│
└── gatto_farioli/        → Local intelligence engine (self-contained Python package)
    ├── run.py            → CLI orchestrator (--ingest, --brief, --radar, --outcomes, --ask, --health)
    ├── config.yaml       → User-editable portfolio, theses, watchlist, sources, outcomes, llm
    ├── ingestion/        → Network fetchers: news, prices, kalshi, polymarket, macro, … (stubs where noted)
    ├── analysis/         → Business logic: narratives, opportunities, radar, brief, outcomes, dialogue, …
    ├── storage/          → schema.py, db.py, state.py, source_health.py — SQLite only
    ├── output/           → Future telegram/email/dashboard stubs
    ├── scripts/verify.py → 27-check integration harness (temp DB)
    ├── tests/            → pytest (offline; mock network/LLM)
    └── PHASE_*.md        → One-shot implementation prompts (one phase = one commit)
```

**RULES**

- **Gatto:** Business logic lives in `analysis/` and `ingestion/`, not in `run.py` (orchestration only). DB access via `storage/db.py` helpers (`get_conn`, `query_one`, `query_all`, `execute`). Schema changes only in `storage/schema.py` (+ migrations in `db.py` when explicitly required by a phase prompt).
- **War Portfolio:** Keep route handlers thin; shared logic in `lib/`. Do not move dashboard state into `argos.db` without an explicit integration task.
- **Phased work:** Follow the relevant `gatto_farioli/PHASE_*_PROMPT.md` file list and scope exactly; do not mix phases in one commit.

---

## Core Rules

- **NEVER** commit secrets, `.env`, `argos.db`, or `argos.db-wal`
- **NEVER** `git stash pop` / `git stash apply` unless Daniel explicitly requests it (stash `wip: future-phase radar narratives kalshi` exists)
- **Gatto phases:** one logical phase per commit; only touch paths listed in that phase’s “Allowed file list”
- **Pre-flight** (start of non-trivial Gatto work): `git branch --show-current`, `git rev-parse HEAD`, `git status --short`, `git stash list`
- Prefer **minimal, surgical** diffs; match existing naming and patterns in the file you edit
- **Python:** use type hints where the module already does; no gratuitous `Any`; wrap external I/O in try/except where runners already do
- **JavaScript:** ESM (`import`/`export`); no `any` (N/A — plain JS); async handlers should handle errors and return sensible HTTP status codes
- **Do not** edit `index.html`, `api/`, `lib/`, `sw.js` during Gatto-only phases unless the prompt allows it
- **Do not** weaken verify/pytest assertions to make CI green

---

## Workflow Rules

- Read `PRODUCT_VISION.md` and the phase prompt (if any) before coding Gatto features
- Ask clarifying questions before large or ambiguous tasks
- Break multi-step work into a short plan, then implement step-by-step
- Smallest working implementation first; no drive-by refactors
- For Gatto bugfixes: reproduce with `pytest` or `scripts/verify.py`, fix root cause, re-run full verification quartet

**Multi-step Gatto tasks**

1. Plan (files + behavior)
2. Implement within allowed paths only
3. `pytest -q` → `scripts/verify.py` → `python3 -m gatto_farioli.run --health` → `git status --short`
4. Completion report per `AGENTS.md`: Branch+HEAD, Files changed, Commands+results, Risks, Next step

---

## Execution Discipline

- Never mark work complete without running the verification commands above
- `scripts/verify.py` uses a **temporary DB** — never rely on it mutating `argos.db`
- `--dry-run` must mean **zero writes** when a phase prompt says so (including `runs`, `briefs`, `opportunity_outcomes`)
- `--ask` does **not** run ingestion; stale data is a user/ops issue, not a reason to call `score_opportunities` from dialogue
- If verify fails on check 24+ after earlier checks populated `opportunity_outcomes`, isolate test data (DELETE or dedicated assertions) — shared temp DB is intentional

---

## Commit Rules

- One logical change per commit (one Gatto **phase** = one commit with the prompt’s exact title when specified)
- Prefixes: `feat(gatto):`, `fix(gatto):`, `feat:`, `fix:`, `docs:`, `refactor:`
- Append when using Cursor for Gatto phases: `Co-authored-by: Cursor <cursoragent@cursor.com>` (if the phase prompt requires it)
- Do not push unless asked

---

## Out of Scope (unless explicitly requested)

- Re-architecting War Portfolio or migrating to TypeScript/Next.js
- UI polish on `index.html` not tied to the task
- New npm or pip dependencies without justification in a phase prompt
- Token budget enforcement, streaming `--ask`, multi-turn chat (deferred post–Phase I)
- Confidence calibration from outcomes, `EXECUTE_NOW`, PortWatch ingestion, Telegram/email alerts
- `git stash pop` and merging unrelated stash work

---

## Cursor / External Execution Rule

When Daniel points at `gatto_farioli/PHASE_*_PROMPT.md` (“execute perfectly”):

- Implement the phase in-repo (do not only output a plan)
- Obey pre-flight, allowed file list, definition of done, and completion report format in the prompt and `AGENTS.md`
- Do not implement code outside the allowed paths; stop and ask if the prompt conflicts with repo state

For **exploratory** or **dashboard-only** tasks without a phase prompt: a short plan with file paths and test commands is acceptable before coding.

---

## Key references

| Doc | Purpose |
|---|---|
| `README.md` | Repo map, dashboard curl examples, Gatto status summary |
| `gatto_farioli/README.md` | Gatto capabilities, daily commands, verify checklist |
| `PRODUCT_VISION.md` | Mission, quality bar, dialogue model, roadmap P1–P3 |
| `AGENTS.md` | Mandatory git checks, verify-before-done, output format |
| `war-portfolio-refactor.md` | Historical refactor notes (read if touching dashboard) |

## Decision Authority

When uncertain:
- Prefer asking over assuming
- Never invent missing architecture in Gatto phases
- If conflict exists between docs → AGENTS.md overrides CLAUDE.md

## Mandatory Rule Sources

Always treat the following files as authoritative and read them before execution:

- PRODUCT_VISION.md
- AGENTS.md
- gatto_farioli/PHASE_*_PROMPT.md (if referenced)
- war-portfolio-refactor.md (if touching dashboard)

If any rule conflicts:
AGENTS.md overrides CLAUDE.md for execution discipline.
PRODUCT_VISION.md overrides for intent and product direction.