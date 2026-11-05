# SDLC Orchestrator Skill

A reusable [Agent Skills](https://agentskills.io) folder for coding-agent hosts.
The `SKILL.md` is the agent-facing playbook; Python scripts provide deterministic
state, resumability, dependency planning, Git worktrees, bounded context packets,
and optional local agent and MR CLI adapters. Python projects can enable an
agent-facing style policy and enforced Ruff/ty/test quality gates.

**Does not:** implement a particular app, guarantee review quality, choose actual
model IDs, install a coding agent, or access your GitHub/GitLab credentials.
The default board is empty until a real project's PRD is approved.

## Files

```text
sdlc-orchestrator/
├── SKILL.md                      # Short on-demand agent instructions
├── README.md                     # Setup, commands, and recovery
├── requirements.txt              # PyYAML
├── requirements-dev.txt          # Ruff, ty, pytest (install in dev environment)
├── pyproject.toml                # Quality policy for THIS skill's Python scripts
├── scripts/
│   ├── sdlc.py                   # User/orchestrator CLI
│   ├── core.py                   # Board/approval validation and scheduling
│   ├── context.py                # Bounded per-ticket + review briefs
│   ├── gitops.py                 # Epic branches, worktrees, commit checks
│   ├── quality.py                # Enforced Ruff/ty/test quality gates
│   ├── recovery.py               # Git and run-log snapshot for interrupted tasks
│   └── decision_query.py         # Extract relevant decisions for a ticket/epic

├── assets/
│   ├── board.yml                 # Empty board template
│   ├── board.example.yml         # Schema and dependency example
│   ├── config.yml                # Model tiers and CLI runner adapters
│   ├── state.yml                 # Approval state template
│   ├── PRD.md                    # PRD outline
│   ├── ARCHITECTURE.md           # File tree/interface proposal outline
│   ├── skeleton.yml              # Blank file manifest
│   ├── skeleton.example.yml      # Function + docstring example
│   ├── DECISIONS.md              # Decision log template
│   ├── pyproject.python.toml     # Python app Ruff/ty template
│   └── python-quality.github-actions.yml # Optional CI config
├── references/
│   ├── TICKET_RULES.md           # Planning and model assignment rules
│   ├── REVIEW_REPORT.md         # Independent review contract
│   ├── PYTHON_STYLE.md          # Concrete Python engineering rules + OSS references
│   └── WORKFLOW_PROPOSALS.md    # Not implemented; approve/veto suggestions
└── tests/
    ├── test_workflow.py          # Git-backed end-to-end tests
    └── test_quality.py           # Quality gate success/failure tests
```

## Setup

Use Python 3.10+ and Git. From the skill folder:

```bash
python -m pip install -r requirements-dev.txt  # orchestration + quality tooling
# In your NEW or EXISTING application repo:
git init -b main                  # only if not already a git repository
python /path/to/sdlc-orchestrator/scripts/sdlc.py --project . init
python /path/to/sdlc-orchestrator/scripts/sdlc.py --project . resume
```

For brevity, the rest of this README assumes:

```bash
export SDLC=/path/to/sdlc-orchestrator/scripts/sdlc.py
# In the app's primary checkout:
python "$SDLC" resume
```

If the base branch isn't `main`, change `.sdlc/config.yml -> base_branch`.
Avoid running control-plane mutating commands from an epic worktree; use
`--project` to target the **primary** checkout.

## Phase gates

1. **PRD:** Iterate with user in `.sdlc/PRD.md`; change `Status: DRAFT` to
   `Status: FINAL` only after consent; `python "$SDLC" approve prd`.
2. **Architecture:** Propose/iterate `.sdlc/ARCHITECTURE.md`; finalize status;
   `python "$SDLC" approve structure`.
3. **Skeleton:** Fill `.sdlc/skeleton.yml` using
   `assets/skeleton.example.yml` as a guide; `python "$SDLC" scaffold`,
   inspect generated file contracts; `python "$SDLC" approve skeleton`.
4. **Plan:** Fill `.sdlc/board.yml` using `assets/board.example.yml`;
   `python "$SDLC" validate`; human approves;
   `python "$SDLC" approve board`.
5. **Commit baseline:** `git add -A && git commit -m
   "Approve initial architecture, skeleton and SDLC plan"`. Git checks ensure
   skeleton files exist on `base_branch` before a ticket starts.
6. **Implement:** `python "$SDLC" next` then `python "$SDLC" start T001`.
   The handoff is `.sdlc/handoffs/T001.md`; a new coding agent reads only this
   brief and specific files it needs. After its own commit:
   `python "$SDLC" finish T001 --checks "pytest tests/test_x.py: PASS"`.
7. **Review:** `python "$SDLC" review E001 --reviewer "GPT Sol (maximum thinking)"`.
   Have an independent reviewer inspect the diff, fix bugs, commit fixes and
   produce the exact report required by `references/REVIEW_REPORT.md`.
   `python "$SDLC" review-pass E001 --reviewer "..." --report report.md`.
8. **MR:** `python "$SDLC" mr E001 --forge github` shows the prepared MR body
   and command; `--create` pushes the branch and invokes authenticated `gh`.
   Use `--forge gitlab` for `glab`. Once merged:
   `git fetch origin && git pull --ff-only`, then
   `python "$SDLC" mark-merged E001` (non-squash merge). A squash merge
   requires `--squash-commit <SHA>` and manual equivalence verification.

**Approval commands must be issued only following actual human confirmation.**
The scripts cannot infer consent from document contents.


## Python code style, Ruff, and ty (NEW)

The initialized `.sdlc/PYTHON_STYLE.md` is copied from
`references/PYTHON_STYLE.md`, so **each fresh coding agent can read a stable
project-local guide**. It draws on CPython's PEP 8/257, Pydantic's Google-style
API documentation, and HTTPX's behavior-first testing practices. Prefer small
functions, explicit input/output/error contracts, type-annotated APIs, clear
exceptions, and tests that reproduce failure modes.

For a **new Python repository**, include a project-specific `pyproject.toml`
based on `assets/pyproject.python.toml` in the approved **skeleton**. It enables:

- Ruff `format` (100-character lines, double-quoted strings) and lint (`F`, `I`,
  `B`, `UP`, `SIM`, `RUF`, `ANN`, `D`, etc.), with Google docstrings for APIs.
- ty's default correctness checks plus explicit import, assignment, return,
  and unused-suppression checks. ty does not expose one global `strict` switch.
- Test exemptions for docstring and annotation rules, but not runtime/type safety.

For an **existing Python repository**, merge the relevant `[tool.ruff]` and
`[tool.ty]` sections into its existing `pyproject.toml` rather than replacing
project metadata. Adapt `src`, `tests`, and the Python target version. Install
`ruff`, `ty`, and `pytest` into the app's development environment, and pin exact
versions in its actual lockfile: ty is still beta and rules can change.

Before `approve board`, configure `.sdlc/config.yml`:

```yaml
quality:
  enabled: true
  commands:
    - [ruff, format, --check, .]
    - [ruff, check, .]
    - [ty, check]
    - [python, -m, pytest, -q]
```

The commands run **in the epic worktree**, not in the primary checkout. A failing
checker blocks `finish` and `review-pass`. The CLI captures each command's exit
status and a small output tail, instead of trusting an agent's textual claim.
Run `python "$SDLC" quality E001` to diagnose a specific epic proactively.
The board gate stores hashes of the enabled quality settings and Python style
policy: changing either after approval requires `reopen board` (only before
implementation). Do not disable quality to force a ticket through.

For GitHub projects, `assets/python-quality.github-actions.yml` is an optional
CI starter. Copy/adapt it during the approved skeleton phase. **Do not** add it
to an existing repo without checking its dependencies/test runner.

Note: The package's Python unit tests were executed locally; Ruff/ty CLI
binaries could not be fetched in this execution environment, so their actual
execution against this package is **not claimed**. The configuration follows
published Ruff/ty docs and is ready for validation where tools are installed.

## Model configuration and spawning

Edit `.sdlc/config.yml`:

```yaml
routing:
  low:    {provider: openai, model: "YOUR_CHEAP_MODEL_ID"}
  medium: {provider: openai, model: "YOUR_BALANCED_MODEL_ID"}
  high:   {provider: xai, model: "YOUR_HIGH_REASONING_MODEL_ID"}
runners:
  openai: ["your-openai-agent-cli", "--model", "{model}", "--prompt-file", "{prompt_file}", "--cwd", "{worktree}"]
  xai: ["your-xai-agent-cli", "--model", "{model}", "--prompt-file", "{prompt_file}", "--cwd", "{worktree}"]
```

**These runner commands are placeholders**, not claims about real CLI flags.
Replace each with an installed, supported client/adapter; choose real IDs from
*your* account. The selected reviewer labels are not guaranteed to be API IDs.
If the host has a native subagent mechanism, use that mechanism directly with
`brief` instead of configuring runners. One fresh subagent per ticket.

For external command adapters, use `python "$SDLC" launch T001 --dry-run` to
inspect the command, then `python "$SDLC" launch T001` to spawn it. It runs in
its epic worktree and redirects its output to `.sdlc/runs/`. Record PID and log;
**do not launch a second agent until the first has exited**. After an interruption,
inspect the log/worktree and re-launch with `--resume` only when safe.

The scripts do **not** call model APIs directly, measure actual provider token
usage, auto-escalate models, or use arbitrary shell strings to execute tasks.

## Resuming after interruption

```bash
python "$SDLC" resume                  # minimal JSON: phase, active, ready, reviews, MR URLs
python "$SDLC" validate                # dependencies + approval hashes
python "$SDLC" brief T001 --max-chars 8000 > /tmp/T001.md
python "$SDLC" recover T001 --max-chars 10000 > /tmp/resume-T001.md
# Inspect worktree files / git diff / agent run log for any interrupted ticket.
# Continue in place; never discard partial changes or launch duplicate agents.
```

`recover` creates a bounded packet from the existing worktree, current diffstat,
commits since ticket start and the last 800 bytes of its runner log. It **does not**
assume that a recorded PID represents the original agent; inspect the process
and avoid overlapping agents. Resume the existing work, not an empty branch.

The tool writes control-plane data atomically and serializes mutations with an
OS-held lock, making interruptions safer. Commit `.sdlc/board.yml`,
`.sdlc/state.yml`, `.sdlc/DECISIONS.md`, and review reports periodically so
progress is recoverable across machines as well as sessions. `handoffs/` and
`runs/` are intentionally gitignored; regenerate handoffs from canonical files.

If an approved plan changes **before implementation**:
`python "$SDLC" reopen prd|structure|skeleton|board`; subsequent approvals
must be repeated. Mid-implementation requirement changes deliberately block
rather than silently rewriting already-started work; decide a formal migration
procedure with the human.

## Tests

```bash
python -m unittest discover -s tests -v
ruff format --check .
ruff check .
ty check
```

Tests use temporary local Git repos; they require no internet, API key, model
account, external provider, or GitHub/GitLab login.

## Optional improvements requiring approval

See `references/WORKFLOW_PROPOSALS.md` for six explicitly **unimplemented**
ideas, including PRD-to-test traceability, integration release reviews,
change requests, actual model cost tracking, safe same-epic parallelism,
and forge-confirmed merges. Each requires a separate approve/veto decision.

## Known limits

- One active ticket per epic because worktrees are **epic-scoped**. Parallel
  tickets in one epic need ticket-isolation branches/cherry-picking (proposal only).
- The `--checks` string is evidence recorded by the orchestrator, not proof
  that tests ran; the reviewer must check it.
- The report syntax/HEAD is verified, but review quality still depends on the
  independent reviewer. Re-review after changes.
- The scheduler doesn't automatically start agents or request model escalation.
- MR creation requires real remotes, `gh`/`glab`, and authentication.
