# Advanced queries API — architecture and repository proposal

Status: PROPOSED — awaiting structure approval. PRD approved on 2026-11-04.

## Architecture

Use Python 3.14, FastAPI/Uvicorn for HTTP, and Psycopg 3 for direct parameterized
PostgreSQL queries. Keep the service small: validate a JSON expression, compile
it into a set of job IDs, select up to the requested limit (default/max 10), and
retrieve job details.
Use a connection per request with context-managed cleanup. A connection pool
and an ORM add little value for this local evaluation service.

The async HTTP boundary reads a bounded request stream and validates JSON;
blocking database work runs in FastAPI's thread pool. No database work blocks
the event loop. Dependency versions are pinned in the skeleton stage; runtime
and development dependency lock files are committed before implementation.
Use standard CPython (not the optional free-threaded build). Verify the pinned
runtime and quality tools in the Python 3.14 Docker image at the skeleton gate.
The earlier 3.12 selection was a conservative default, not a demonstrated
compatibility requirement; current Psycopg and FastAPI releases support 3.14.

## Repo tree

```text
.
├── README.md                     # Setup, API contract, curl examples, DBeaver
├── Dockerfile                    # Python runtime, non-root API process
├── docker-compose.yml            # Supplied database + API, health dependencies
├── .dockerignore                 # Exclude Git, caches, artifacts, workflow files
├── .gitignore
├── pyproject.toml                # Package metadata, Ruff, ty, pytest configuration
├── requirements.txt              # Pinned runtime dependency closure
├── requirements-dev.txt          # Pinned quality/test dependencies
├── src/sumble_api/
│   ├── __init__.py
│   ├── app.py                    # HTTP routes, bounded body reading, error mapping
│   ├── query.py                  # Typed expression nodes and strict validation
│   ├── compiler.py               # Expression -> SQL job-ID set and bound values
│   ├── database.py               # PG settings, execution, timeout, job hydration
│   ├── models.py                 # Job response and public error models
│   └── observability.py          # Request IDs, JSON logs, persistent rotation
├── tests/
│   ├── conftest.py               # HTTP and isolated database fixtures
│   ├── test_query.py             # Grammar and resource-limit validation
│   ├── test_compiler.py          # Structure and parameter binding checks
│   ├── test_api.py               # HTTP errors, body bounds, response contract
│   ├── test_database.py          # Postgres Boolean semantics and real examples
│   ├── test_concurrency.py       # Admission cap, overload, cleanup under failures
│   └── test_observability.py     # Non-200 logging, redaction, rotation
├── docs/
│   ├── database.md               # Observed schema and join rules
│   └── validation.md             # Measured timing, environment, checks, limitations
├── scripts/
│   └── package.py                # Build a ZIP from an explicit artifact allowlist
├── dist/                         # Generated submission ZIP; ignored by Git
└── .sdlc/                        # Workflow source of truth; excluded from ZIP
```

No application or skeleton files are created until structure approval. Existing
skill files under `.agents/` remain workflow tooling and are excluded from the
application image and ZIP.

## Observed database schema

Inspected the supplied image on 2026-11-04, digest
`sha256:714ff812e4d1ceb832ac41bc1a7d471cd5485fe14cb7a01b705909933a11097d`.

| Table | Relevant columns | Rows |
| --- | --- | ---: |
| job_posts | id, organization_id, country_id (bigint); datetime_pulled (timestamptz) | 89,397 |
| job_posts_details | id (bigint), job_title, location (text) | 89,397 |
| organizations | id (bigint), name (text) | 5 |
| tech | id (bigint), name (text) | 9,413 |
| job_functions | id (bigint), name (text) | 170 |
| job_posts_tech | job_post_id, tech_id (bigint) | 405,050 |
| job_posts_job_functions | job_post_id, job_function_id (bigint) | 80,811 |

No indexes or declared constraints exist. Observed IDs are unique in job_posts,
job_posts_details, organizations, tech, and job_functions. Details link to a job
by equal `id`; organization links through `organization_id`; relationship tables
link jobs to dictionaries. Do not parse the denormalized text columns
`job_posts.technologies` or `job_posts.job_functions`.

## Component responsibilities

- `query.py`: validate the complete request envelope and build immutable typed
  leaf/AND/OR/NOT nodes. Traverse raw objects with explicit depth/node checks
  before recursive compilation. Reject unknown keys and invalid node shapes.
- `compiler.py`: compile leaves from hard-coded table/column templates. Return
  SQL text with `%s` placeholders and ordered bound values. No request-controlled
  SQL identifiers or value interpolation.
- `database.py`: read standard PG environment variables, connect with a 3-second
  connection timeout, apply a 25-second statement timeout, run one search
  statement, map rows to response models, and close the connection. All search
  transactions are read-only; no schema migration or index build at startup.
- `models.py`: typed serialized job and error models shared by HTTP and data code.
- `observability.py`: server-generated request IDs, safe structured request/error
  logs, bounded file rotation, and logging setup. No database error-log table.
- `app.py`: create the app; implement POST `/jobs/search`, GET `/health`, and GET
  `/ready`; bound incoming bodies, map errors, and expose `/docs` with expression
  grammar and request examples. No SQL generation in routes.

## Public interfaces and contracts

Planned signatures (precise annotations and docstrings are part of the skeleton):

- `parse_query(payload: object) -> SearchRequest`: accepts the entire decoded request;
  returns a validated expression and result limit; raises `QueryValidationError` with a safe path
  and explanation for an invalid envelope, expression, value, or resource limit.
- `compile_query(expression: Expression) -> CompiledQuery`: accepts a validated
  tree; returns the job-ID SQL set and ordered parameters without executing SQL.
- `search_jobs(expression: Expression, limit: int = 10) -> list[Job]`: executes a validated search;
  returns zero to ten jobs in ascending ID order; propagates typed connection
  or statement-timeout failures for HTTP mapping.
- `check_database() -> None`: runs a short readiness probe; raises on unavailable
  database or missing required relations/columns.
- `create_app() -> FastAPI`: constructs routes, response models, and error handling.
- `configure_logging(log_dir: Path) -> None`: configures JSON stdout logging and
  rotating non-200 event files; reports file-sink failures through stdout.
- `build_submission(output: Path) -> Path`: packages the explicit deliverable
  allowlist and returns the ZIP path; fails when required inputs are missing.

POST `/jobs/search` uses the approved `{"query": expression}` contract.
User confirmed the optional `limit` member during architecture discussion:
`{"query": expression, "limit": 5}`. Default 10; accept only integers from 1
through 10, explicitly rejecting booleans, floats, numeric strings, and null.
Apply a bound SQL limit parameter. This retains the approved maximum of 10 jobs;
pagination, custom ordering, projection, and client-selected timeouts remain
excluded. This is compatible with the PRD's fixed maximum and adds no pagination.
Leaf fields are exactly `technology`, `job_function`, and `organization`.
Match with database `lower(name) = lower(%s)`; no fuzzy matching, alias matching,
or trimming of nonempty input values. Reject whitespace-only strings.

Each successful job object contains:

```json
{
  "id": 123,
  "organization_id": 5,
  "organization_name": "Apple",
  "job_title": "Example title",
  "location": "Example location",
  "country_id": 1,
  "datetime_pulled": "2022-12-01T05:14:42.522991Z"
}
```

IDs are JSON integers. Descriptive fields and source nullable columns permit
null; `id` is required. Missing details/organizations do not remove a matching job:
hydrate through LEFT JOINs. Times are ISO 8601 with timezone. Response is a bare
array, not an envelope. Technologies/functions are used for filtering and are
not expanded into response arrays.

Proposed request limits: 16,384 bytes, 128 expression nodes including operators,
16 expression levels (root level 1), and 256 characters per leaf value. AND/OR
require at least one child; NOT has a single expression child. No implicit
unfiltered search: the query member is mandatory. Enforce byte limits on actual
streamed input even when Content-Length is missing or inaccurate.

Errors use `{"error":{"code":"...","message":"..."}}` with optional safe
validation path, never raw SQL, credentials, or driver exception text:

| Condition | HTTP status |
| --- | ---: |
| Malformed JSON or parser recursion failure | 400 |
| Incomplete body after its read deadline | 408 |
| Unsupported request content type | 415 |
| Oversized body | 413 |
| Invalid expression/envelope or expression limits | 422 |
| Database connection failure/unavailable database | 503 |
| Search capacity exhausted (code `server_busy`, Retry-After: 1) | 503 |
| Statement timeout | 504 |
| Unexpected server failure | 500 |

`/health` is process liveness (200). `/ready` checks database connectivity and
required schema (200 or 503). A ready response does not promise a specific search
will finish. Search failures never become successful empty results.

## Data flow and key constraints

1. Bound the incoming stream, decode JSON, validate the envelope and expression.
2. Each leaf yields a set of job IDs via its relationship/dictionary join.
   An organization leaf joins job_posts to organizations.
3. Parenthesized `INTERSECT` implements AND, `UNION` implements OR, and
   `SELECT id FROM job_posts EXCEPT (child)` implements NOT.
4. Intersect the result with actual jobs, order by job ID, and apply the validated
   limit (default/max 10) in a
   selected-ID CTE before hydrating descriptive fields. Preserve ordering in the
   final SELECT. Set operators deduplicate relationship matches. No `NOT IN`
   null semantics or correlated unindexed lookup per job.
5. Return typed job objects; close/rollback connections even on cancellation.

Run database work in one SQL statement for a consistent snapshot. Match names in
Postgres so case rules are consistent across fields. Treat absent/null association
names as no match; EXCEPT correctly includes these jobs under negation.

## Packaging, local execution, and verification

### Performance and bounded concurrency

Async functions have no inherent minimum or maximum concurrency. Database work
is the scarce resource here. Proposed deployment defaults:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Uvicorn workers | 1 | Makes the application-wide admission cap unambiguous |
| MAX_CONCURRENT_SEARCHES | 4 | At most four admitted searches, including body reading and validation |
| Idle database connections | 0 | Open per request and close after completion |
| Readiness probe capacity | 1 | Separate slot; concurrent probes fail fast instead of piling up |
| Waiting search queue | 0 | Reject excess requests immediately with 503 and Retry-After |
| Connection timeout | 3 seconds | Bounds a new database connection attempt |
| Search statement timeout | 25 seconds | Server cancels expensive search SQL |
| Readiness statement timeout | 2 seconds | Short schema/connectivity check |
| Request body read deadline | 5 seconds | Incomplete uploads return 408 and release the slot |

Cap MAX_CONCURRENT_SEARCHES configuration to integers 1–16 for this local service;
use 4 unless measured load tests justify a change. With one worker, at most five
database operations run at once by default (four searches plus readiness), well
below the observed PostgreSQL max_connections of 100. Limits are per process:
increasing workers multiplies the connection bound and needs explicit tuning.
The limit is a resource budget, not a guarantee of requests per second.

Acquire admission without waiting, before reading the search body; release in
finally blocks after the worker and database cleanup actually finish. Run Psycopg
in an AnyIO worker thread with abandon_on_cancel disabled: cancelling an HTTP
task must not free capacity while its SQL continues uncounted. Keep `/health`
independent of search slots. Use the bounded body reader for admitted requests;
other excess requests fail before allocating a body buffer.

Preserve the under-30-second required example after readiness, measured through
HTTP, without claiming a 30-second deadline for every possible request. The SQL
timeout covers execution, not connection time, upload time, or serialization.
If a client disconnects, SQL may run until completion/timeout; its slot stays
occupied until cleanup. Document that behavior rather than promising immediate
server cancellation.

Measure both required examples at concurrency 1 and 4 on the real image; record
latency distribution, throughput, errors, and environment. Exercise an overload
above four held searches and verify 503s, responsive health checks, no leaked
slots/connections, and recovery after success, exception, timeout, and disconnect.
The existing 255 ms SQL probe is only a single-query baseline. LIMIT bounds the
response and hydration, but set evaluation may still scan complete tables.

### Injection protections

SQL injection prevention is in scope. Bind every user value through Psycopg's
execute(sql, params), including the optional integer limit. Only predefined
templates supply identifiers and operators; field/operator allowlists never
accept raw SQL fragments. Build grouping from validated expression nodes.
Do not concatenate or format user values into SQL and do not rely on manually
escaping apostrophes. Literal injection-like names must remain data, with the
same semantics as any other name.

Read-only transactions, request bounds, and query timeouts are additional
defenses, not replacements for binding. The assignment uses a postgres
superuser; read-only mode is not an authorization boundary. A separately
provisioned SELECT-only role would further restrict a production deployment,
but is not required to alter this evaluation dataset. Verify injection-like
values through actual Postgres tests as well as compiler parameter checks.

### Error logging and storage

Log every completed HTTP request with status other than 200, including invalid
routes and overload responses. Use application middleware/exception handlers
so validation errors and unexpected exceptions follow the same path. Include
UTC timestamp, server-generated request ID, method, normalized route (or a fixed
unmatched-route label), status, safe error code, and duration_ms. Add the request
ID to response headers and error bodies for correlation. Use INFO for non-error
non-200 statuses, WARNING for 4xx, and ERROR for 5xx. Also record an aborted
request event when no HTTP response can be delivered.

Write JSON lines to stdout for `docker compose logs application`, and non-200
events to `/var/log/sumble/errors.jsonl` on a named `app_logs` volume. Rotate at
5 MiB with three backup files (about 20 MiB total). This retains recent logs
through container recreation and ordinary `docker compose down`; explicit volume
removal deletes them, and old entries expire on rotation. Document inspection
and deletion commands. Prepare volume permissions for the non-root runtime.
Use one file-writing process under the single-worker deployment. Configure
Docker stdout log rotation at 10 MiB with three files as well.

Never log credentials, authorization headers, request bodies, query values,
database driver messages, or SQL with bound values. For unexpected exceptions,
record exception class and sanitized stack frame locations without locals or
raw exception text. Disable duplicate verbose framework tracebacks that would
bypass sanitization. Keep record fields bounded and JSON-encoded to prevent log
forgery. File-write failures fall back to stdout and produce a safe diagnostic;
logging failure must not convert a successful search into an error response.
This is bounded local operational history, not an indefinite audit archive.

Test status coverage, request-ID correlation, redaction with injected secret-like
values, file rotation, and persistence across application container recreation.
Do not rely on the search database for error storage: logs must work during a
database outage. Runtime logs/volumes are excluded from Git, images, and ZIPs.

### Runtime and submission

Compose uses the supplied database image pinned to the observed digest, a
`pg_isready` database health check, and the application built from Dockerfile.
Bind API `127.0.0.1:8000` and database `127.0.0.1:5432` for local evaluation and
DBeaver. API uses PGHOST=db and the assignment's remaining PG defaults. Use
database health dependencies and an API readiness health check. Preserve the
image's database layout rather than mounting an empty volume over its dataset.

The temporary inspection container currently owns local port 5432; stop it before
Compose verification. It is not a project runtime dependency. Do not disturb
unrelated containers.

Use Ruff formatting/lint, ty type checking, and pytest, following the skill's
Python template. Proposed standard test command: `python -m pytest -q`.
Pure validation/compiler/HTTP tests need no Docker. Integration tests require an
explicit test database configuration, create an isolated test schema with tiny
fixtures, and roll back/remove only that schema. They verify real SQL semantics
for missing/null values, repeated associations, AND across distinct associations,
operator nesting, and injection-like literal names. Never mutate supplied data.

Run real-image checks for both required queries and independently verify returned
job IDs against straightforward SQL. Record HTTP timings after readiness and
verify Apple AND .net is under 30 seconds. The discovery SQL probe returned 3 jobs
in 255.470 ms on this host; this is not yet an API performance result. The second
required discovery query also returned 3 jobs. Record full runtime validation in
docs/validation.md once implementation exists.

Build the final ZIP from an allowlist; inspect its contents and verify extraction
and Compose startup. Upload/form completion follows artifact creation and any
required submission identity details.

## Tradeoffs and alternatives

- SQL set algebra fits the existing unindexed, modest dataset and gives explicit
  Boolean semantics. It may rescan relationship tables for repeated leaves;
  expression limits bound this cost. No speculative indexing work is needed for
  the observed performance. A larger production dataset would justify indexes
  and query-plan tuning.
- Correlated EXISTS is concise but can repeatedly scan unindexed associations;
  joins also require care to avoid duplicates and to support AND across values.
- Direct Psycopg SQL makes the query visible and testable. An ORM adds another
  expression translation layer without helping this fixed schema.
- A connection per request favors simplicity for local use. Higher concurrency
  could justify pooling later; this design bounds concurrency with admission slots.
- No runtime data mutation, frontend, parser for a textual query language,
  pagination, or total counts are introduced.

## Primary documentation

- PostgreSQL set operations: https://www.postgresql.org/docs/current/queries-union.html
- Psycopg bound parameters: https://www.psycopg.org/psycopg3/docs/basic/params.html
- FastAPI request modeling: https://fastapi.tiangolo.com/tutorial/body-nested-models/
- Python support status: https://www.python.org/downloads/
- FastAPI Python 3.14 support: https://fastapi.tiangolo.com/release-notes/#01183
- Psycopg supported systems: https://www.psycopg.org/psycopg3/docs/basic/install.html
- AnyIO worker cancellation: https://anyio.readthedocs.io/en/stable/threads.html
- Docker log rotation: https://docs.docker.com/engine/logging/drivers/json-file/

## Approval decision

Pending explicit human approval of structure, boundaries, response fields,
validation limits, concurrency defaults, logging policy, and standard test command.
User confirmed the repo tree and optional limit; full architecture approval is
pending after the six-question discussion recorded in DECISIONS.md.
No skeleton or code yet.

