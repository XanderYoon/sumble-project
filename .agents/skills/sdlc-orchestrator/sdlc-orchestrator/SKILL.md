---
name: sdlc-orchestrator
description: Guide an interactive, approval-gated software development lifecycle from PRD through repo design, scaffolding, dependency-aware ticket planning, isolated coding agents, independent epic review, and GitHub/GitLab merge requests. Use for starting, continuing, or resuming a structured multi-agent coding project while minimizing context and model cost.
compatibility: Python 3.10+, PyYAML 6.x, Git; optional configured coding-agent CLI and GitHub/GitLab CLI
metadata:
  version: "1.1"
---

# SDLC orchestrator

Operate as the **control-plane orchestrator**, not an all-purpose implementation agent.
Be concise with the user; be detailed in the PRD and ticket acceptance criteria.
The main checkout's `.sdlc/` directory is the persisted source of truth.
Run the script from the skill folder, supplying `--project <primary_repo_checkout>`.
If not initialized, run `python scripts/sdlc.py --project <repo> init`.
Read `references/TICKET_RULES.md` only when constructing/dispatching the board.
For Python tickets, follow the project-local `.sdlc/PYTHON_STYLE.md` and
`pyproject.toml`. Never pass the full style guide to every agent; handoffs
provide a short reminder and an on-demand file path.

## Always start by resuming

1. Run `python scripts/sdlc.py --project <repo> resume` and `... validate`.
2. Follow `phase` and `next_step`; do not assume the stage from conversation memory.
3. If implementation is active, inspect active tickets, their worktree status, commits,
   and `.sdlc/runs/` logs before dispatching or retrying agents. Never blindly spawn
   a second agent against a still-active worktree.
4. If an approved document or plan hash changed, **stop**. Ask the human to reconcile
   the change. Prior to starting code, `... reopen <stage>` resets affected approvals.
   Once code has started, require an explicit change-management decision; do not erase
   ticket history, silently rewrite specs, or bypass the stale-approval guard.
5. Never treat an agent-generated decision, checklist, or CLI invocation as human
   approval. Call `approve` only after explicit human confirmation in conversation.

## Gate 1 — product requirements

Draft the PRD together with the user. Prefer `.sdlc/PRD.md` (Markdown). Optionally produce `docs/PRD.html` when diagrams or richer rendering help; keep the canonical approved content in `.sdlc/PRD.md` for the control plane. Ask clarifying questions in small, focused batches about actors, user journeys, triggers, data rules, errors, edge cases, performance/security constraints, integrations, and exclusions. Prefer observable examples and exact success/failure scenarios over vague features. Challenge contradictory requirements; keep an `Open questions` section until resolved. **Do not** pick product semantics on the user's behalf. Distinguish requirements from optional suggestions.

Include diagrams (UML, sequence, ERD, architecture) **only when necessary** for clarity. Prefer Mermaid or simple embedded SVG. Store supporting diagrams and other documentation under `docs/`. Mark the PRD FINAL only after the user confirms it, then run `... approve prd`. No repo structure or code yet.

## Gate 2 — proposed architecture and file tree

Propose a concrete repo tree in `.sdlc/ARCHITECTURE.md`, with responsibilities,
module boundaries, data/control flow, and contracts. Mention alternatives and
material tradeoffs; avoid unnecessary abstraction. Encourage a top-level `docs/`
directory for all project documentation (PRD, architecture notes, diagrams, decisions).
Iterate with the human until approved. Finalize the document, then run `... approve structure`.
Do **not** create skeleton files before this approval.

## Gate 3 — code skeleton

Populate `.sdlc/skeleton.yml` with approved files and stub content. For public
functions/methods, use concise docstrings specifying **inputs, outputs, behavior,
and error conditions**. Include minimal tests scaffolding; no ticket business logic.
For Python projects, adapt `assets/pyproject.python.toml` into the skeleton,
include locked Ruff/ty/pytest tooling, and agree on a test command. Do not
replace an existing `pyproject.toml`; merge tool sections instead. Run
`... scaffold` (idempotent; never overwrites files), show the tree/signatures to
human, obtain their explicit approval, then `... approve skeleton`.
Commit the approved skeleton to the base branch *before starting tickets*.

## Gate 4 — epics and tickets

Translate the final PRD into `.sdlc/board.yml`. Read `references/TICKET_RULES.md`.
Each epic is a self-contained capability with acceptance and `depends_on` epics.
Each ticket is a bounded independently committable task with acceptance, test
commands, scoped file paths, referenced PRD/architecture headings, and intra-epic
`depends_on` tickets. Assign `difficulty: low|medium|high` conservatively; route
to the chosen OpenAI/xAI model through `.sdlc/config.yml`. Use low for routine
work, medium for complexity, high only for genuine hard problems. Avoid invented
API model IDs. `... validate` checks dependency cycles and structure.
For Python repos set `.sdlc/config.yml -> quality.enabled: true` with
Ruff format, Ruff lint, ty, and test argv commands *before* approval. Review
quality settings with the human: quality/style policy hashes are frozen at
board approval. Present the plan for explicit human approval; then `... approve board`.
Do not change approved plan content mid-implementation without change control.

## Gate 5 — ticket execution, one fresh agent per ticket

1. `... next` returns eligible tickets; schedule at most one ticket per epic
   worktree at once. Independent epics may run in parallel. Within an epic,
   respect ticket dependencies; do not start a ticket until its `depends_on`
   tickets are done. Use the brief for the assigned model tier.
2. `... start T001` creates/reuses `epic/e001-...` worktree, records starting
   commit and creates `.sdlc/handoffs/T001.md`.
3. `... brief T001 --max-chars 10000` emits *only* objective, acceptance,
   relevant requirement sections, dependencies, scoped paths, tests, and selected
   model tier. Pass this brief to a **fresh** coding subagent. Use native subagents
   in the host if available; otherwise `... launch T001` uses a configured
   provider runner; it does nothing without a real adapter in `config.yml`.
4. Technical implementation questions: agent decides independently using code,
   tests and accepted architecture. When ambiguous **product behavior** blocks
   execution, ask the human using short options and implications, then log the
   decision via `... decision T001 --choice "..." --why "..."`. Decisions land
   in `.sdlc/DECISIONS.md`. Query relevant prior decisions when building a brief
   if the log has grown.
5. Agent works in the epic worktree, runs ticket tests, and makes a scoped git
   commit. Agent does **not** edit the primary checkout board or state.
6. Orchestrator verifies the worktree is clean and the ticket has a new commit,
   **re-runs configured quality commands in the worktree**, then calls
   `... finish T001 --checks "actual command and observed results"`. `finish`
   blocks on failed formatting, lint, typing, or tests; never bypass the gate.
7. Repeat until epic tickets are done. State updates are written to disk after
   each step. Never claim tests passed unless observed.

## Gate 6 — independent epic review and merge request

When all tickets in an epic are done, run
`... review E001 --reviewer "GPT Sol (maximum thinking)"` (or the configured
Grok reviewer). Spawn an **independent high-reasoning reviewer** using
`.sdlc/handoffs/review-E001.md`; inspect actual diff and run tests. Log real
findings with file references. Fix legitimate issues in the epic worktree and
commit fixes separately; re-review the final HEAD. Use
`references/REVIEW_REPORT.md` for the report format. A PASS must have an exact
`Reviewed HEAD: <SHA>` with a `Verdict: PASS` and no blocking findings.
Then call `... review-pass E001 --reviewer "..." --report <path>`;
this re-runs the configured Python quality gate after review fixes.

Run `... mr E001 --forge github|gitlab` to draft the description; add
`--create` only after remote/authentication are configured. Record manual MRs
with `... mr-created E001 --url https://...`. Once the MR is actually merged,
fetch/update the base branch and call `... mark-merged E001` (or explicitly
verify a squash merge before `--squash-commit`). Only then can dependent epics
start. Do not merge unreviewed changes or modify a reviewed HEAD without a
new review.

## Interruptions and context discipline

- On every new session, use `resume` first. Trust persisted metadata plus Git
  over memory. Read only referenced sections, short handoffs, and changed files.
- `.sdlc/state.yml`: approvals/phase. `.sdlc/board.yml`: dependency/status/commits.
  `.sdlc/DECISIONS.md`: rationale. `.sdlc/runs/`: optional runner logs.
- An interrupted in-progress ticket remains in progress; inspect existing
  uncommitted work and log, then resume with a new agent only if the previous
  one is no longer active. Pass the refreshed ticket brief and a short local
  Git status/diff summary; do not restart from zero. Run
  `... recover T001 --max-chars 10000` to produce a bounded restart packet
  with current worktree state, recent commits and the prior agent log tail.
- Use script output, not huge logs or full PRD/repo dumps, as subagent context.
- Keep the human informed with brief progress and genuine decision questions.
