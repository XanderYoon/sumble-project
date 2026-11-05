# Validation record

Skeleton checks observed on Linux amd64 using CPython 3.14.10 in Docker:

- Runtime and development dependency installations succeeded with pinned versions.
- `docker compose config --quiet` passed.
- `docker build -t sumble-skeleton:review .` passed.
- `ruff format .` reported 18 files unchanged; the explicit format check also passed.
- `ruff check .` passed.
- `ty check` passed.
- `python -m pytest -q` passed: one import-safety test, 0.43 seconds.

Pinned quality tools: Ruff 0.17.3, ty 0.0.97, pytest 9.2.0. Runtime roots:
FastAPI 0.162.0, Uvicorn 0.54.0, Psycopg/psycopg-binary 3.3.8,
AnyIO 4.16.0, Pydantic 2.14.2. Dependency closures are in requirements files.
The smoke test verifies that importing modules does not open a database;
it does not exercise search behavior. The app factory and business functions
are explicit NotImplementedError stubs, so API startup is not yet expected.

No HTTP search performance or Boolean correctness is verified at this stage.
A prior read-only SQL discovery probe for Apple AND .net returned three rows
in 255.470 ms; this is not an API benchmark or evidence for the revised sort
and pagination implementation. Final checks must use the supplied image and
record environment, HTTP timing, concurrency behavior, and limitations.
