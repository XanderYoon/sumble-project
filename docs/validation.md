# Validation record

Measured on 2026-11-05 UTC (2026-11-04 America/New_York), Linux x86_64,
kernel 7.0.0-38-generic, Intel Core i7-4790K @ 4 GHz (4 cores/8 threads),
15 GiB RAM, Docker Engine 29.4.1. Host test interpreter CPython 3.14.2;
container base Python 3.14-slim is pinned by digest in Dockerfile. Supplied
PostgreSQL 14.11 image digest:
`sha256:714ff812e4d1ceb832ac41bc1a7d471cd5485fe14cb7a01b705909933a11097d`.
API UID/GID observed as 10001/10001; database PID 1 runs as postgres.
Only `sumble-schema-inspection` was stopped before Compose claimed port 5432;
no public dataset writes or indexes were used. Tests use disposable schemas.

These measurements describe the service as verified on that date. A later
review change kept the same search contract and SQL, and simplified internals:
query validation walks the expression recursively, JSON decoding no longer
applies a separate 64-container scan, database work uses
`anyio.to_thread.run_sync` with `abandon_on_cancel` disabled, and request logs
record the exception class without stack frames. The figures below were not
re-run after that change.

## Locked gates

- `ruff format --check .`: passed (19 Python files).
- `ruff check .`: passed.
- `ty check`: passed.
- `.venv/bin/python -m pytest -q`: **218 passed, 33 skipped**, 55.49 seconds.
  Skips are opt-in PostgreSQL integration cases.
- With PGHOST=127.0.0.1, PGPORT=5432, PGUSER=postgres,
  PGPASSWORD=supersecretpassword, PGDATABASE=sumble_data, SUMBLE_RUN_INTEGRATION=1:
  `.venv/bin/python -m pytest -q -m integration`: **33 passed**,
  218 deselected, 23.16 seconds. Actual PostgreSQL timeout/cancellation,
  connection cleanup, sorting and Boolean semantics passed.
- Both test commands emit one upstream FastAPI/Starlette TestClient deprecation
  warning about httpx. Locked dependencies were retained.
- `docker compose config --quiet`: passed.
- `docker compose up --build --wait --wait-timeout 180`: passed; both healthy.

Pinned tools: Ruff 0.17.3, ty 0.0.97, pytest 9.2.0. Runtime roots:
FastAPI 0.162.0, Uvicorn 0.54.0, Psycopg/psycopg-binary 3.3.8,
AnyIO 4.16.0, Pydantic 2.14.2. Full closures are in requirements files.

## Real-image HTTP acceptance

Times below measure localhost HTTP request through full response body after
readiness, using Python urllib and perf_counter. Startup/download time excluded.
A is the README Apple AND .net request; B is NOT Apple AND (statistician OR psql).
No client retries were used.

| Request | HTTP | IDs | End-to-end ms |
| --- | --- | --- | ---: |
| A | 200 | 184916, 491622, 553333 | 276.197 |
| B | 200 | 371692, 395736, 4354640 | 411.284 |

The required default A request is under 30 seconds on this environment.
Independent SQL probes used ordinary EXISTS joins rather than the API set
compiler and returned the same complete ID sets, ordered by job ID:

```sql
-- A
SELECT j.id FROM job_posts j
JOIN organizations o ON o.id=j.organization_id
WHERE lower(o.name)='apple'
AND EXISTS (SELECT 1 FROM job_posts_tech r JOIN tech t ON t.id=r.tech_id
            WHERE r.job_post_id=j.id AND lower(t.name)='.net') ORDER BY j.id;
-- B
SELECT j.id FROM job_posts j
WHERE NOT EXISTS (SELECT 1 FROM organizations o
                  WHERE o.id=j.organization_id AND lower(o.name)='apple')
AND (EXISTS (SELECT 1 FROM job_posts_job_functions r
             JOIN job_functions f ON f.id=r.job_function_id
             WHERE r.job_post_id=j.id AND lower(f.name)='statistician')
     OR EXISTS (SELECT 1 FROM job_posts_tech r JOIN tech t ON t.id=r.tech_id
                WHERE r.job_post_id=j.id AND lower(t.name)='psql')) ORDER BY j.id;
```

SQL-only probe times were 54.785 ms (A) and 539.771 ms (B); these are not HTTP benchmarks.

## Distribution and overload

Each concurrency run issued 20 alternating A/B requests using a thread pool,
new urllib HTTP requests, no application warmup beyond the acceptance requests.
P95 is the nearest-rank 19th of 20 samples. Throughput includes client scheduling
and full batch elapsed time. Small local samples are observations, not a service
level guarantee; other host activity and cache state can affect results.

| Concurrency | N | Min ms | Median ms | P95 ms | Max ms | Requests/s | Statuses |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 20 | 188.699 | 299.748 | 361.818 | 449.885 | 3.480 | 20 × 200 |
| 4 | 20 | 207.721 | 314.569 | 432.526 | 437.424 | 11.759 | 20 × 200 |

Sorted raw timing samples in milliseconds:

- Concurrency 1: 188.699, 204.34, 210.114, 233.966, 238.34, 247.849, 253.395, 259.406, 281.682, 298.934, 300.561, 301.248, 304.501, 308.65, 314.469, 319.208, 325.546, 342.534, 361.818, 449.885.
- Concurrency 4: 207.721, 233.421, 241.94, 245.674, 255.633, 267.159, 270.974, 278.559, 300.506, 305.554, 323.584, 328.997, 335.129, 349.664, 373.54, 384.503, 390.676, 431.028, 432.526, 437.424.

A burst of 32 B requests with client concurrency 16 returned four 200 responses
and 28 safe 503 `server_busy` responses, with no other errors. Admission stays
bounded at four; cancellation/slot-retention behavior is additionally verified
by unit and actual PostgreSQL integration tests.

## Sorts, offsets, and statement deadlines

All four sort fields in both directions were requested over HTTP for A:

| Field/direction | HTTP | IDs | ms |
| --- | --- | --- | ---: |
| id_asc | 200 | 184916, 491622, 553333 | 180.025 |
| id_desc | 200 | 553333, 491622, 184916 | 217.372 |
| datetime_pulled_asc | 200 | 184916, 491622, 553333 | 281.699 |
| datetime_pulled_desc | 200 | 553333, 491622, 184916 | 242.134 |
| job_title_asc | 200 | 491622, 184916, 553333 | 924.127 |
| job_title_desc | 200 | 184916, 553333, 491622 | 752.113 |
| organization_name_asc | 200 | 184916, 491622, 553333 | 193.257 |
| organization_name_desc | 200 | 184916, 491622, 553333 | 190.865 |

The integration suite independently checks ordering, nulls-last, ID tie breaks,
paging continuity and beyond-end pages using controlled fixture data.
A at offset 100000 returned 200 with an empty array in 180.953 ms.
B with timeout_ms=1 returned 504 `statement_timeout` in 12.372 ms;
a later overload batch and searches succeeded, demonstrating recovery.

A broad `NOT technology=nonexistent-tech` request at offset 80000 returned
**504 `statement_timeout` in 25018.140 ms** with default timeout_ms=25000.
This is a recorded performance limitation, not a successful deep-result page.
No arbitrary query is guaranteed to finish within the statement deadline.
Read-only EXPLAIN (without ANALYZE) showed an underestimated EXCEPT output
(200 rows estimated versus a broad complement), followed by a nested loop join
filter `job.id = ANY_subquery.id` against the materialized ID set. The plan
estimated 36,291,434 rows at the final merge join. Relevant plan excerpt:

```text
Limit (cost=21479.03..21479.31 rows=10 width=128)
  Merge Left Join (cost=19235.62..1036942.86 rows=36291434 width=128)
    Nested Loop (cost=6609.49..479519.17 rows=83590 width=64)
      Join Filter: (job.id = "ANY_subquery".id)
      Nested Loop Left Join
        Gather Merge -> Sort -> Parallel Seq Scan on job_posts job
        Materialize -> Seq Scan on organizations
      Materialize (cost=0.00..13226.89 rows=200 width=8)
        Subquery Scan on "ANY_subquery"
          HashSetOp Except (cost=0.00..13223.89 rows=200 width=12)
            Append -> job_posts scan / distinct technology leaf
    Materialize -> Sort -> Seq Scan on job_posts_details
```

This plan evidence was reported for independent review; T007 did not modify
core SQL or the supplied database to hide the limitation.

## Persistent logs and clean source delivery

A real statement-timeout event populated `/var/log/sumble/errors.jsonl`.
The entire file was captured before and after
`docker compose up --force-recreate --no-deps --wait --wait-timeout 60 application`;
byte comparison passed. This verifies the `app_logs` volume survives API
recreation. Rotating-file bounds/redaction are tested; rotation thresholds are
5 MiB plus three backups. Docker JSON logging is separately bounded at 10 MiB
and three files per service. README documents read/delete/retention commands.

The packaging helper has been implemented and statically checked only. Its
explicit 26-file allowlist includes source, tests, documentation, requirements,
pyproject, Docker and ignore files. It rejects missing/nonregular/symlinked
inputs including symlinked ancestors, validates inputs before opening a new
exclusive output, and does not discover or include secrets/workflow/cache/DB
files. Existing outputs are not overwritten. Runtime packaging checks are
**deferred**, including missing-input/symlink archive tests, actual archive
inspection, extraction, and startup from extraction. No archive of any format
was created and `build_submission` was never invoked. No Google form submission
or upload was performed.

Clean-source startup was verified from `/tmp/sumble-clean-source-review`:
26 explicitly allowlisted files were copied with `shutil.copy2` into a new
otherwise-empty directory, without invoking the packaging function. No virtual
environment, Git metadata, workflow files, logs, or database files were copied.
After stopping only the epic's running stack to release its localhost ports,
`docker compose -p sumble-clean-review -f /tmp/sumble-clean-source-review/docker-compose.yml up --build --wait --wait-timeout 180`
built the API and started a separate database/container network and fresh log
volume; both services became healthy. `/ready` and both README curl requests
succeeded with the exact required IDs. The temporary stack was taken down
without deleting its log volume, and the original epic stack was restarted.
This checks copied-source startup; it does **not** verify any archive contents
or extraction behavior. Those checks remain deferred until user approval.
