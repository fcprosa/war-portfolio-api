# AGENTS.md — Execution Contract

This file defines mandatory execution rules for all Claude Code operations in this repository.

---

## 1. Pre-Execution Snapshot (Mandatory for non-trivial work)

Before making changes, always run:

- git branch --show-current
- git rev-parse HEAD
- git status --short
- git stash list

Do NOT proceed if working directory is unstable without acknowledgment.

---

## 2. Phase Discipline

- Never mix multiple phases in a single commit
- Never modify files outside the active phase scope
- If a task is not explicitly part of a phase, STOP and ask

---

## 3. Stash Safety

- NEVER run `git stash pop`
- NEVER apply stashed changes unless explicitly instructed
- Stash is considered external memory state

---

## 4. Verification Gate (Hard Stop Rule)

Before marking ANY task complete, you MUST run:

- pytest -q
- python3 -m gatto_farioli.run --health
- python gatto_farioli/scripts/verify.py
- git status --short

If any step fails:
→ STOP
→ fix root cause
→ re-run full verification set

---

## 5. Output Contract (Mandatory Format)

Every completed task must include:

- Branch name + HEAD hash
- List of modified files
- Commands executed + results
- Risks / assumptions
- Next step recommendation

---

## 6. Failure Handling

If tests or verification fail after multiple attempts:

- Stop execution
- Re-evaluate assumptions
- Ask for clarification before continuing

No infinite retry loops.

---

## 7. Core Principle

Correctness > completion speed  
Verification > assumption  
Simplicity > complexity