# Board design and model routing (read when planning or dispatching)

## Epic
Each epic is one independently reviewable product capability (not a folder or sprint).
It has a user-visible or internally testable goal, explicit acceptance criteria,
and `depends_on` epic IDs. Every epic runs in exactly one dedicated Git worktree
and branch. Epics with no unfinished dependencies can run concurrently.

## Ticket
Each ticket is a single bounded change that can be committed independently.
Provide: `title`, actionable `description`, `difficulty`, `depends_on`,
`files` (paths to inspect/change), relevant PRD/architecture section names,
observable `acceptance` items, and executable `tests` (strings).
Use **intra-epic** ticket dependencies only; model cross-epic dependencies at
`epic.depends_on`. Avoid tickets defined as "Implement the whole feature".

Difficulty heuristics:
- `low`: straightforward edit/test, limited choices, no complex invariants.
- `medium`: multiple interacting modules, nontrivial state, migration, or complex testing.
- `high`: difficult concurrency/consistency, algorithmic invariants, security-critical
  design, architectural risk, or significant unknowns. Do not label high merely
  because a ticket touches several files.

Model is selected by `config.yml -> routing[difficulty]` at dispatch time.
Prefer an inexpensive model for routine work; escalate only after a *concrete*
failed attempt or identified high-risk issue and document why. Provider and model
IDs are user-configured, because available models and pricing vary by client.
The desired review labels are separate from executable API model IDs.

## Orchestrator hygiene
The primary checkout owns `.sdlc/board.yml` and `.sdlc/state.yml`.
Subagents edit only their epic worktree. The orchestrator alone calls `start`,
`finish`, `review-pass`, and MR state transitions in the primary checkout.
Do not let two agents write the same epic worktree concurrently. Use the
script's short context packet rather than giving every agent full conversation logs.

## Python quality policy
Every Python ticket follows `.sdlc/PYTHON_STYLE.md`. A Python repo's approved
skeleton should contain `pyproject.toml` with Ruff and ty configuration,
`requirements-dev.txt` or equivalent locked tool dependencies, and a small test
suite. Turn on `config.yml -> quality.enabled` when these are installed. The
control plane re-runs configured checks before recording ticket completion and
before passing an epic review. Do not bypass failures by weakening configuration.
