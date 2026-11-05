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
a JSON array (default/max 10).

Names match case-insensitive exact equality (`.net` matches `.NET`, not
`Dotnet`; `apple` matches `Apple`, not `Apple Services`). Missing associations
fail a positive leaf and satisfy NOT. See [docs/database.md](docs/database.md)
and [docs/code-flow.md](docs/code-flow.md).

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
| Docs | http://127.0.0.1:8000/docs |
| Ready | `GET /ready` (database + schema) |
| Live | `GET /health` |

Compose starts the API and the supplied database. Ports bind to localhost only.
If a leftover `sumble-schema-inspection` container owns port 5432, stop that
container first.

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
optional.

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

# Page, sort, and statement timeout
curl --fail-with-body -sS -X POST http://127.0.0.1:8000/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"query":{"field":"organization","value":"apple"},"limit":3,"offset":3,"sort":{"field":"job_title","direction":"desc"},"timeout_ms":1000}'
```

Success is a bare JSON array of job objects (`id`, `organization_id`,
`organization_name`, `job_title`, `location`, `country_id`, `datetime_pulled`).
No total count. Advance `offset` by `limit`; fewer than `limit` rows is the last
page. Each request is one snapshot; data changes between pages can skip or
repeat jobs.

| Control | Default | Allowed |
| --- | --- | --- |
| `limit` | 10 | integer 1–10 |
| `offset` | 0 | integer 0–100000 |
| `timeout_ms` | 25000 | integer 1–25000, SQL only |
| `sort.field` | `id` | `id`, `datetime_pulled`, `job_title`, `organization_name` |
| `sort.direction` | `asc` | `asc`, `desc` |

Nulls sort last; non-ID ties use ID ascending. Body must be UTF-8 JSON, at most
16 KiB. Invalid queries return 422; timeouts 504; overload 503 (`Retry-After: 1`).
Errors look like `{"error":{"code":"...","message":"...","request_id":"..."}}`.

## Develop

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q

# Compose must be up. Tests create/drop isolated schemas only.
env PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres \
  PGPASSWORD=supersecretpassword PGDATABASE=sumble_data SUMBLE_RUN_INTEGRATION=1 \
  .venv/bin/python -m pytest -q -m integration
```

```bash
docker compose logs --tail=100 application
docker compose down
```
