# Advanced queries on Sumble

On [sumble.com/jobs](https://sumble.com/jobs), listings default to simple
filters: technology **or** job function, not both. Power users need nested
Boolean filters across several fields, for example:

```text
(tech: python AND tech: sql AND organization: amazon)
OR
(job_function: software engineer AND tech: sql)
```

This repo is a local Python 3.14 / FastAPI service over a supplied PostgreSQL
subset. `POST /jobs/search` accepts a JSON expression with AND, OR, and NOT on
`technology`, `job_function`, and `organization`, and returns matching jobs as
a JSON array (default/max 10). Names are literal and case-insensitive
(`.net` matches `.NET`, not `Dotnet`; `apple` matches `Apple`, not
`Apple Services`). Missing associations fail a positive leaf and satisfy NOT.

See [docs/database.md](docs/database.md), [docs/code-flow.md](docs/code-flow.md),
and [measured validation](docs/validation.md).

## Run the program

Docker with Compose, from this directory:

```bash
docker compose up --build --wait --wait-timeout 180
curl --fail-with-body http://127.0.0.1:8000/ready
```

| | |
| --- | --- |
| API | http://127.0.0.1:8000 |
| Search | `POST /jobs/search` |
| Docs | http://127.0.0.1:8000/docs (generated route list; this README is the contract) |
| Ready | `GET /ready` (database + schema) |
| Live | `GET /health` |

Images are pinned by digest. The API runs as UID 10001. Ports bind to localhost
only. Compose waits for database health and keeps error files in `app_logs`.
`/health` is process liveness; `/ready` checks schema on a separate bounded
connection. Initial image download/build/startup is outside search timings.
If a leftover `sumble-schema-inspection` container owns port 5432, stop that
container first. Preserve unrelated containers.

### Database

The dataset is Docker image `nzsne8pttnh8wrpu/p2ckp9ddret2arc2`. Compose already
runs it. To start **only** the database (do not also run Compose on the same
port):

```bash
docker run --rm -p 5432:5432 nzsne8pttnh8wrpu/p2ckp9ddret2arc2
```

| | |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `5432` |
| Username | `postgres` |
| Password | `supersecretpassword` |
| Database | `sumble_data` |

DBeaver can use the same settings. Do not alter public dataset rows.

## Search

Leaves are `{"field":"...","value":"..."}`. `and`/`or` take a nonempty array of
expressions; `not` takes one expression. Query is required; other members are
optional. Every response includes a server-generated `X-Request-ID` header.

On the supplied image, the first required request returns IDs 184916, 491622,
553333; the second returns 371692, 395736, 4354640.

```bash
# Spec example: (python AND sql AND amazon) OR (software engineer AND sql)
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"or":[{"and":[{"field":"technology","value":"python"},{"field":"technology","value":"sql"},{"field":"organization","value":"amazon"}]},{"and":[{"field":"job_function","value":"software engineer"},{"field":"technology","value":"sql"}]}]}}'

# Required: Apple AND .net
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"and":[{"field":"organization","value":"apple"},{"field":"technology","value":".net"}]}}'

# Required: NOT Apple AND (statistician OR psql)
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"and":[{"not":{"field":"organization","value":"apple"}},{"or":[{"field":"job_function","value":"statistician"},{"field":"technology","value":"psql"}]}]}}'

# Nested expression, second page, descending text sort, one-second SQL timeout
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"and":[{"field":"organization","value":"apple"},{"or":[{"field":"technology","value":"python"},{"not":{"field":"job_function","value":"statistician"}}]}]},"limit":3,"offset":3,"sort":{"field":"job_title","direction":"desc"},"timeout_ms":1000}'
```

Success is a bare JSON array of job objects (`id`, `organization_id`,
`organization_name`, `job_title`, `location`, `country_id`, `datetime_pulled`).
No total count. Distinct technology AND leaves may match different associations
on the same job. Duplicate associations do not duplicate jobs.

| Control | Default | Allowed |
| --- | --- | --- |
| `limit` | 10 | integer 1–10 |
| `offset` | 0 | integer 0–100000 |
| `timeout_ms` | 25000 | integer 1–25000, SQL statement only |
| `sort.field` | `id` | `id`, `datetime_pulled`, `job_title`, `organization_name` |
| `sort.direction` | `asc` | `asc`, `desc` |

Either sort member may be omitted. Nulls sort last; non-ID ties use ID
ascending. Text follows database collation. Advance `offset` by `limit`; fewer
than `limit` rows is the last page (a full last page may need an empty
follow-up). Each request is one snapshot; inserts, deletes, or sort-value
changes between pages can skip or repeat jobs.

Inputs must be UTF-8 `application/json`: 16384 actual body bytes, five seconds
to read the body, 128 expression nodes, 16 expression levels (root is 1), 256
characters per nonblank leaf. Expression depth and node violations are 422.
JSON that cannot be decoded, including input that overflows the JSON parser,
is 400. Unknown keys, ambiguous nodes, null controls, and non-integer controls
are rejected. Values are not trimmed.

## Errors and bounded work

Errors look like `{"error":{"code":"...","message":"...","request_id":"...",
"path":"..."}}`; `path` is optional. Public errors and application logs omit
driver text, credentials, bodies, and leaf values.

| HTTP | Meaning |
| --- | --- |
| 400 | malformed, non-UTF-8, or otherwise undecodable JSON |
| 408 | body-read deadline exceeded |
| 413 | body larger than 16384 bytes |
| 415 | content type is not application/json |
| 422 | invalid query or controls |
| 503 | database unavailable, or `server_busy` admission rejection |
| 504 | database statement timeout |
| 500 | unexpected internal failure |

Unknown routes are 404; wrong methods are 405. Four searches are admitted per
process by default, with no queue; overload is 503 plus `Retry-After: 1` before
reading the body. Compose uses one Uvicorn worker. `MAX_CONCURRENT_SEARCHES`
can be 1–16. Readiness has its own one-slot capacity. Connection setup is 3s.
`timeout_ms` is the SQL statement only, not an HTTP deadline. Client disconnect
does not release admission until worker cleanup finishes. No partial page is
returned after statement cancellation.

## Develop

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/ty check
.venv/bin/python -m pytest -q

# Compose must be up. Tests create/drop isolated schemas only.
env PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres \
  PGPASSWORD=supersecretpassword PGDATABASE=sumble_data SUMBLE_RUN_INTEGRATION=1 \
  .venv/bin/python -m pytest -q -m integration
```

Integration credentials need schema creation and session inspection. Public
dataset rows are untouched.

```bash
docker compose logs --tail=100 application
docker compose exec application tail -n 100 /var/log/sumble/errors.jsonl
# Stop the sole writer, then remove only the named error files; preserve volume.
docker compose stop application
docker compose run --rm --no-deps application python -c 'from pathlib import Path; [(Path("/var/log/sumble") / n).unlink(missing_ok=True) for n in ("errors.jsonl", "errors.jsonl.1", "errors.jsonl.2", "errors.jsonl.3")]'
docker compose start application
# Removes this stack's containers/network; retains app_logs.
docker compose down
```

Every request writes one JSON line to stdout (request ID, route, status, code,
duration). Non-200 and aborted requests are also appended to `errors.jsonl`.
An unexpected exception is recorded by class name only. File rotation is 5 MiB
plus three backups. App logs persist through recreation. `docker compose down --volumes`
deletes the log volume.

## Delivery status

The source is ready for code review. `scripts/package.py` implements an explicit
source/config/tests/docs/lock allowlist and refuses symlinked inputs or missing
files. Packaging execution, archive inspection, and startup from an extracted
archive are **deferred until approval of the entire codebase**. No archive was
created. Clean startup was instead checked from a fresh copied source directory.
Do not upload or submit to the Google form.
