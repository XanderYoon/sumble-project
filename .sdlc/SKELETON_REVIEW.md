# Skeleton review

Status: approved by the user with "LGTM" after reviewing the final diagram.
No implementation or ticket execution yet.
PRD v2 and architecture v2 approvals are recorded in state.yml.

## Created tree

```text
README.md
Dockerfile
docker-compose.yml
.dockerignore
pyproject.toml
requirements.txt
requirements-dev.txt
src/sumble_api/
  __init__.py
  app.py
  query.py
  compiler.py
  database.py
  models.py
  observability.py
tests/
  conftest.py
  test_api.py
  test_query.py
  test_compiler.py
  test_database.py
  test_concurrency.py
  test_observability.py
docs/
  code-flow.md
  database.md
  validation.md
scripts/package.py
```

Existing .gitignore was extended to keep .sdlc and .dockerignore visible while
excluding runtime/worktree artifacts. The complete file/content manifest is
.sdlc/skeleton.yml; scaffold never overwrites existing files.

The requested diagram in [docs/code-flow.md](../docs/code-flow.md) shows the
intended request path and module boundaries; it does not imply implementation.

## Public signatures

```python
parse_query(payload: object) -> SearchRequest
compile_query(expression: Expression) -> CompiledQuery
search_jobs(request: SearchRequest) -> list[Job]
check_database() -> None
create_app() -> FastAPI
configure_logging(log_dir: Path) -> None
build_submission(output: Path) -> Path
```

Expression nodes: Leaf, Conjunction, Disjunction, Negation. SearchRequest carries
an Expression and immutable SearchOptions; SQL compilation returns a CompiledQuery
with SQL and separately bound parameters. Job/ErrorDetail/ErrorResponse describe
public responses. All public functions document inputs, outputs/behavior, and
errors; implementation functions currently raise NotImplementedError.

Options default to limit=10, offset=0, sort_field=id, sort_direction=asc,
timeout_ms=25000. Validators, compiler, routes, SQL execution, admission limits,
logging, and archive building are not implemented. The Docker image builds,
but the stub app factory intentionally prevents API startup.

## Tooling and observed checks

Python 3.14.10; Ruff 0.17.3; ty 0.0.97; pytest 9.2.0. Runtime/development
dependency closures are pinned and were installed in this runtime. Both Docker
base images are pinned by digest. Docker runtime is non-root; Compose declares
local ports, database readiness, four search slots, persistent log volume, and
bounded Docker log rotation. Runtime controls are contracts until implemented.

- Docker image build and Compose config validation passed.
- Ruff format check, Ruff lint, and ty passed.
- Standard test command `python -m pytest -q`: one import-safety test passed.
- No search correctness, route behavior, concurrency, logging persistence, or
  end-to-end performance checks have been claimed.

Development setup: `python -m pip install -r requirements-dev.txt` with Python
3.14. Quality commands: `ruff format --check .`, `ruff check .`, `ty check`, and
`python -m pytest -q`. Integration tests will require an explicitly configured
isolated PostgreSQL schema and arrive with implementation tickets.

## Next gate

`approve skeleton` succeeded. Commit the approved skeleton/control documents to
main before any ticket execution, then propose epics/tickets and quality
configuration for a separate board approval.
