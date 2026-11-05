# Implementation plan review

Status: APPROVED — user confirmed with "LGTM". PRD, architecture, and
skeleton approvals are recorded. Approved baseline commit: `76cb973` on main.

## One epic, seven independently committed tickets

E001 delivers the single user-facing capability: advanced Boolean job search,
bounded execution, and a validated Docker/submission package. One epic avoids
introducing cross-epic merge dependencies for this small, cohesive service.

| Ticket | Change | Difficulty | Depends on |
| --- | --- | --- | --- |
| T001 | Strict Boolean/query-option validation | medium | — |
| T002 | Parameterized SQL set compiler | medium | T001 |
| T003 | Real Postgres execution, sort/page/timeout, schema readiness | medium | T002 |
| T004 | Safe JSON error logs and persistent rotation | medium | T003 |
| T005 | HTTP search/health/readiness/docs and error integration | medium | T003, T004 |
| T006 | Admission limits, cancellation accounting, cleanup recovery | high | T005 |
| T007 | Real-image acceptance/performance, Docker delivery, ZIP, README | medium | T006 |

Each ticket's scoped files, acceptance examples, requirement references, and
test commands are in board.yml. Dependencies are intentionally sequential:
agents share one epic worktree and must not overwrite concurrent work. T006
merits high difficulty because cancellation must not release a slot while its
database thread still executes. No other ticket needs a high tier.

## Execution and model routing

Start each ticket with the SDLC CLI, then dispatch its bounded brief to a fresh
native coding agent in the epic worktree. Agents commit only their scoped work;
the orchestrator verifies commits/clean status, reruns checks, and records finish.
Implementation agents never edit the primary board/state.

Available host model IDs configured in config.yml:

| Tier | Model | Reasoning |
| --- | --- | --- |
| low (not currently used) | gpt-6-luna | low |
| medium | gpt-6.1-sol | medium |
| high | gpt-6.1-sol | high |
| Independent epic reviewer | gpt-6.1-sol | max |

After all tickets, use a fresh independent reviewer, inspect the actual diff,
and run tests. PASS must identify the exact reviewed HEAD and have no blocking
findings. Fixes are separate commits and require review of the final HEAD.
The GitHub remote and CLI authentication are configured; draft a merge request
after a passing review and quality gate. Do not mark a merge complete before
the merge actually happens.

## Quality policy proposed for approval

quality.enabled is true. Commands run from each worktree using its ignored local
.venv, avoiding dependence on the system interpreter/tool versions:

```text
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/ty check
.venv/bin/python -m pytest -q
```

The standard test command remains `python -m pytest -q` after activating .venv.
Use the approved Ruff/ty policy in pyproject.toml and .sdlc/PYTHON_STYLE.md.
Board approval freezes quality configuration/style hashes; failed checks are
not bypassed or weakened. The orchestrator reruns configured checks at ticket
finish and independent review pass, plus the relevant integration commands
when database behavior is changed.

Observed planning validation: dependency/schema validation passed; every ticket
PRD/architecture heading resolves. The exact configured quality runner passed
Ruff format (19 files), Ruff lint, ty, and the one import-safety test (1.04 s)
on the local scaffold. These results are setup evidence, not implementation
correctness or HTTP performance evidence.

Worktree preparation uses uv (already available), Python 3.14, and the committed
requirements-dev.txt. On this host the managed interpreter and download cache
are under /tmp; these are local tooling, not submission dependencies:

```bash
UV_PYTHON_INSTALL_DIR=/tmp/sumble-python UV_CACHE_DIR=/tmp/sumble-uv-cache uv python install 3.14
UV_PYTHON_INSTALL_DIR=/tmp/sumble-python UV_CACHE_DIR=/tmp/sumble-uv-cache uv venv --python 3.14 .venv
UV_CACHE_DIR=/tmp/sumble-uv-cache uv pip sync --python .venv/bin/python requirements-dev.txt
```

Local quality interpreter: CPython 3.14.2 from the installed uv catalog. The
Docker runtime is the separately verified/pinned CPython 3.14.10 image. Both
use the same committed dependency/tool versions. Final acceptance runs in Docker.

## Integration and delivery verification

Integration tests opt in via SUMBLE_RUN_INTEGRATION=1 and standard PG settings.
Tiny fixtures create/drop only a dedicated temporary schema; never alter public
assignment rows. Real timeout/cancellation checks use controlled slow fixtures.
User input is tested against actual Postgres, not only mocked driver calls.

The final ticket stops only the earlier inspection container if it owns port
5432, starts Compose, and validates both required examples against independent
SQL results. Measure default Apple AND .net under 30 seconds, concurrency 1/4,
overload/recovery, all sort fields/directions, and a deep offset. Verify persisted
logs after container recreation. Include environment and real observations in
docs/validation.md; do not reuse discovery timing as final API evidence.

Build dist/sumble-advanced-queries.zip from an explicit allowlist, inspect contents,
and verify startup from clean extraction. Rebuild/recheck the archive after any
review fixes affecting its contents so it reflects the final reviewed code.
Form upload/identity details are handled after a concrete local ZIP exists.

## Approval requested

User approved the ticket board, dependency order, model routing/reviewer,
quality commands, and integration/delivery checks with "LGTM". Record with
`approve board` before ticket execution.
