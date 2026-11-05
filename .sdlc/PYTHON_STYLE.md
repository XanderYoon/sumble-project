# Python engineering style — agent contract

Applies to **Python code** written by coding subagents. Prefer readability, explicit
behavior, small diffs, and tests over clever abstractions. Follow an existing
repo's conventions unless they undermine correctness or an approved requirement.
The associated `pyproject.toml` is executable policy; this file covers judgment
that a linter/type checker cannot enforce.

## Influences (read the source, do not copy another project's code)

- **CPython / PEP 8 and PEP 257:** conventional names, imports, clear public
  interfaces, docstrings, and consistency within a module.
  https://peps.python.org/pep-0008/ and https://peps.python.org/pep-0257/
- **Pydantic:** concise, Google-style API docstrings; explicitly documented
  inputs, returns, and errors; validate data at boundaries.
  https://github.com/pydantic/pydantic/blob/main/docs/contributing.md
- **HTTPX:** disciplined public APIs, small reproducible bug reports and
  behavior-focused testing, including failure modes.
  https://github.com/encode/httpx/blob/master/docs/contributing.md
- **Astral / Ruff and ty:** formatting and static analysis as routine development
  checks, not optional end-of-project cleanup.
  https://docs.astral.sh/ruff/ and https://docs.astral.sh/ty/

These are reference practices, **not** claims that this project follows every
rule in those upstream repositories.

## Code structure

1. **Make the simple path obvious.** Prefer plain functions and standard-library
   types to frameworks, inheritance trees, and generic helpers unless required.
   One module should have one clear responsibility; isolate I/O from core logic.
2. **Names explain intent.** Use `snake_case` for functions/variables/modules,
   `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Avoid abbreviations
   beyond accepted domain terms. Never shadow builtins (`list`, `id`, `type`).
3. **Keep functions focused.** Extract repeated logic or branches that hide
   intent; don't create one-line wrappers solely to appear modular. Avoid global
   mutable state. Prefer explicit dependencies over hidden singletons.
4. **Bound side effects.** Pass `Path` objects for paths; explicitly choose text
   encoding; use context managers for files; separate network, filesystem,
   subprocess, and database operations from pure transformations.
5. **Errors carry meaning.** Raise specific exceptions with actionable context.
   Don't silently swallow errors, catch broad `Exception` without re-raising,
   or turn exceptional results into ambiguous `None`/`False` without a contract.
6. **Safety and efficiency.** Parameterize SQL; avoid `shell=True` and unsafe
   deserialization; handle user-provided paths defensively; use timeouts on
   external calls; avoid repeated I/O in loops when a simple batch suffices.

## Types and documentation

7. **Type all new/modified function signatures**, including `-> None`, and
   annotate non-obvious variables. Use `str | None`, `list[T]`, `Mapping[K,V]`,
   `TypedDict`/dataclasses for structured data, and `Protocol` at genuine
   interface boundaries. Use `Any` only with a reason, not to silence ty.
   If project supports Python 3.10, avoid newer syntax (e.g. PEP 695 generics).
8. **Docstrings describe contracts, not syntax.** Public APIs and nontrivial
   helpers need concise Google-style docstrings. Include Args/Returns/Raises
   when relevant, plus invariants/side effects; don't restate type annotations.
   A trivial private helper may need only a one-line summary.
9. **Comment why, not what.** Write comments for tricky invariants, concurrency
   assumptions and non-obvious tradeoffs. Remove stale and commented-out code.
   Keep architecture decisions in `.sdlc/DECISIONS.md`, not enormous source comments.

Example:

```python
from pathlib import Path


def load_config(path: Path) -> dict[str, str]:
    """Read the application's string-valued configuration.

    Args:
        path: UTF-8 JSON file to read.

    Returns:
        Validated settings keyed by name.

    Raises:
        FileNotFoundError: The config does not exist.
        ValueError: The JSON is invalid or fields have invalid types.
    """
    ...
```

## Testing and code review

10. **Test behavior, not implementation detail.** Cover acceptance criteria,
    invalid input, boundary conditions, exceptions, and regressions. Favor
    deterministic isolated tests over time-sensitive or order-dependent tests.
11. **Do not mask failures.** Use real assertions, not success-looking logs.
    Avoid mocks for straightforward pure logic; use temporary directories and
    local fakes for external boundaries. Keep each test focused on one behavior.
12. **Keep changes scoped.** No unrelated renames/refactors in a ticket. A
    ticket has one commit with a descriptive message; rerun checks after fixes.
13. **Quality gate must be green.** From the Python project root run:

    ```bash
    ruff format --check .
    ruff check .
    ty check
    python -m pytest -q
    ```

    Apply `ruff format .` and `ruff check --fix .` locally before rechecking.
    Fix actual issues rather than adding blanket `# noqa`, `# type: ignore`, or
    broad `[tool.*]` exclusions. Scoped suppressions need a brief rationale.

## Enforcement boundary

- `ruff` handles consistent formatting, imports, common correctness hazards,
  docstring conventions, and new-code type-annotation coverage.
- `ty` checks type consistency and unresolved imports using installed project
  dependencies. It is still beta and **does not have a universal `strict = true`**;
  configure individual rules and pin its version in the real project's lockfile.
- Runtime tests, design review, and security reasoning are still necessary;
  static checks are *not* proof of correctness.
- For scaffolds, stubs with `raise NotImplementedError` are temporary contracts.
  Before an implementation ticket is marked done, remove unresolved stubs.
