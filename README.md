# Sumble advanced queries

Status: code skeleton for review. Query validation, SQL generation, HTTP routes,
concurrency controls, and logging are explicit NotImplementedError stubs.
The API is not functional yet; do not use this scaffold as the submission ZIP.

## Runtime and planned startup

Python 3.14, FastAPI, Psycopg 3, and the supplied PostgreSQL Docker image.
Once implementation is complete, run `docker compose up --build`, then use
http://127.0.0.1:8000/docs. Readiness: `/ready`; liveness: `/health`.
The current factory stub deliberately prevents API startup.

## Approved request contract

POST `/jobs/search` with JSON. Success is a bare array of at most 10 jobs.
Query leaves use field/value; operators are and/or arrays and unary not.
Names match case-insensitive exact equality; absent values satisfy NOT.

```json
{
  "query": {"field": "organization", "value": "apple"},
  "limit": 10,
  "offset": 0,
  "sort": {"field": "id", "direction": "asc"},
  "timeout_ms": 25000
}
```

All controls are optional. Limit: 1–10; offset: 0–100000; timeout_ms: 1–25000
for database statement execution only. Sort fields: id, datetime_pulled,
job_title, organization_name; direction asc/desc; nulls last, ID ascending
breaks non-ID ties. Advance offset by limit. Cross-page snapshots are not
guaranteed if data changes. Defaults are shown above.

## Development checks

With Python 3.14, install `python -m pip install -r requirements-dev.txt`.
Run `ruff format --check .`, `ruff check .`, `ty check`, and
`python -m pytest -q`. Current test coverage is import safety only;
integration and acceptance tests arrive with their implementation tickets.

## Required examples (available after implementation)

```bash
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"and":[{"field":"organization","value":"apple"},{"field":"technology","value":".net"}]}}'

curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"and":[{"not":{"field":"organization","value":"apple"}},{"or":[{"field":"job_function","value":"statistician"},{"field":"technology","value":"psql"}]}]}}'
```

## Optional database inspection

DBeaver: PostgreSQL, host 127.0.0.1, port 5432, database sumble_data,
username postgres, password supersecretpassword. These are assignment defaults.
Runtime PG settings are in Compose; readiness handles database availability.
Stop the temporary sumble-schema-inspection container before starting Compose
if it still owns port 5432. Do not stop unrelated containers.

## Remaining delivery documentation

Implementation tickets will add nested-query, paging/sorting/timeout curl
examples, full error/default/limit documentation, safe persistent log access
and deletion instructions, test-database setup, measured validation results,
and submission ZIP packaging instructions.
