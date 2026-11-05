# Advanced queries on Sumble — product requirements

Status: FINAL v2 — approved by the user with "LGTM" after reviewing the revised PRD and architecture.
The approved v1 is preserved at `.sdlc/revisions/PRD-v1-approved.md`.

## Problem and goals

Build a Python API that searches the supplied Sumble PostgreSQL dataset using
nested Boolean expressions across job technologies, job functions, and
organizations. Deliver runnable source, Docker packaging, documented curl
examples, and a submission ZIP.

## Users and key workflows

Power users compose a JSON expression and receive matching jobs. Evaluators
extract the submission, run `docker compose up --build`, and execute documented
curl commands against a local API. The developer can inspect the same local
PostgreSQL database using DBeaver.

## Functional requirements

1. Search jobs using leaves with fields `technology`, `job_function`, or
   `organization`, combined with arbitrarily nested AND, OR, and unary NOT,
   within documented resource limits.
2. Match names by case-insensitive exact equality. `apple` matches `Apple`,
   but does not match `Apple Services`. Technology punctuation is literal:
   `.net` and `psql` are ordinary names.
3. A leaf is true if the job has at least one associated value with that name.
   Separate technology leaves may match separate associations on the same job:
   AND python and sql requires both technologies, not one association matching both.
4. NOT negates the complete child expression. Missing/null associations cannot
   satisfy a leaf; NOT of that leaf does match a job with no such association.
   These matching and missing-value rules were confirmed by the user.
5. Return each matching job once per page, with optional `limit` (integer 1–10,
   default 10). Support offset pagination with optional `offset` (nonnegative
   integer, default 0). Proposed safety bound: offset at most 100,000; reject
   larger values instead of silently clamping. An offset beyond the matching
   result set returns an empty array.
6. Read from database image `nzsne8pttnh8wrpu/p2ckp9ddret2arc2`, database
   `sumble_data`. Discover actual tables, relationships, and identifier types
   before specifying SQL and the final response schema.
7. Include both required curl examples and a nested AND/OR example in the README.
8. Allow one client-selected sort field: `id`, `datetime_pulled`, `job_title`,
   or `organization_name`, with `asc` or `desc` direction. Defaults are `id`
   and `asc`. Put nulls last in both directions. For equal non-ID sort values,
   use ID ascending as a deterministic tie-breaker. Sort all matches before
   applying offset and limit. Text sorting uses source text and database
   collation; matching remains case-insensitive exact equality.
9. Allow optional `timeout_ms` (integer 1–25,000, default 25,000) for the database
   search statement. Reject zero, negative, or above-maximum values; zero must
   never disable the server timeout. This timeout excludes connection setup,
   HTTP body reading, and response transfer. Timeout produces HTTP 504 with
   no partial results, closes/rolls back the connection, and releases capacity
   after database work actually ends.
10. Defaults make existing query-only requests valid. The user confirmed offset
    pagination, the four-field sorting allowlist, and statement-only timeout
    semantics in the scope-change discussion.

## Nonfunctional requirements

- Python implementation, Dockerfile, and `docker-compose.yml`; a fresh
  `docker compose up --build` starts the supplied database and API, with
  database readiness handled before search requests are served.
- The required Apple AND .net request with default options must complete in less than 30 seconds
  on the supplied dataset after service readiness. Record measured end-to-end
  request timing and the local environment used; report any unverified bound.
  Container/image download and initial service startup are outside this timing.
- Use bound SQL parameters for values and an allowlist for query fields.
  Do not expose raw database errors or credentials in HTTP responses.
- Bound expression size/depth and request body size; document exact limits in
  the architecture before implementation. Reject invalid expressions before SQL.
- Keep database credentials configurable through the supplied PostgreSQL
  environment variables. Compose defaults use the assignment credentials.
- Verify Boolean logic, multi-value associations, missing values, duplicates,
  invalid requests, SQL injection resistance, pagination, sorting, timeout
  cancellation/cleanup, and real-dataset execution. SQL values and pagination
  controls must be bound parameters; sort identifiers/directions must map only
  to predefined allowlisted SQL fragments.
- Use one statement snapshot per page. Offset pagination does not promise a
  stable snapshot across requests when source data changes. Document possible
  repeats or omissions after intervening inserts, deletes, or sort-value updates.
- Local evaluation scope; public hosting and production authentication are excluded.

## Interfaces, inputs, outputs, and error behavior

Proposed endpoint: `POST /jobs/search` with `Content-Type: application/json`.

The request has a required `query` member containing exactly one expression:

```json
{"query":{"and":[{"field":"organization","value":"apple"},{"field":"technology","value":".net"}]}}
```

All controls are optional and may be supplied independently:

```json
{
  "query": {"field": "organization", "value": "apple"},
  "limit": 10,
  "offset": 0,
  "sort": {"field": "id", "direction": "asc"},
  "timeout_ms": 25000
}
```

If `sort` is omitted, use `id` ascending. If it is present, its omitted `field`
or `direction` uses the same default; reject unknown members and explicit null.
Integer controls reject booleans, floats, numeric strings, and null, with HTTP
422. Unknown sort fields/directions, negative/over-limit offsets, and invalid
timeout/limit values also return 422 before executing search SQL.

Expression forms:

- Leaf: `{"field":"technology","value":"python"}` (nonempty string).
- AND: `{"and":[expression, expression]}` (one or more children).
- OR: `{"or":[expression, expression]}` (one or more children).
- NOT: `{"not":expression}` (exactly one child).

Reject unknown fields, extra properties, ambiguous nodes, empty AND/OR arrays,
non-string values, malformed JSON, and expressions exceeding documented limits.
Return a documented client-error response; exact status mapping and error shape
will be specified in the architecture. Do not silently ignore invalid inputs.

Success returns HTTP 200 and a JSON array of job objects, or `[]` for no matches
at the requested offset. Keep the bare-array response; no total count or cursor
metadata. Clients advance offset by limit. Fewer than limit results means the
last page; a full page may require one more request to discover the end.
The actual job fields will be documented after schema inspection; include the
job identifier and available descriptive job data. Database unavailability or
timeout returns a documented non-success status, never an empty successful list.

Required second example:

```json
{"query":{"and":[{"not":{"field":"organization","value":"apple"}},{"or":[{"field":"job_function","value":"statistician"},{"field":"technology","value":"psql"}]}]}}
```

## Out of scope

String-query parsing, simple-filter UI, frontend development, user accounts,
cursor pagination, multi-field custom sorting, total counts, ranking, fuzzy matching, database content editing,
and deployment to a hosted service. DBeaver is an optional inspection client,
not a runtime dependency. Form submission is a delivery step after a concrete
ZIP is available, subject to browser access and any required user form details.

## Acceptance scenarios and edge cases

1. Apple AND .net returns only jobs satisfying both leaves, at most 10 unique
   jobs, and completes in under 30 seconds against the real dataset.
2. NOT Apple AND (statistician OR psql) excludes all Apple jobs and requires at
   least one of the two positive leaves.
3. (python AND sql AND amazon) OR (software engineer AND sql) preserves grouping.
4. Jobs with both python and sql match their conjunction even when represented
   by distinct relationship rows; repeated associations do not duplicate jobs.
5. A job without technologies fails a positive technology leaf and satisfies
   its negation. Double NOT has the same result as the original expression.
6. Mixed-case names match exactly; partial names and SQL metacharacters are
   treated as literal values. An unknown name has no positive matches.
7. Empty operators, unsupported fields, malformed bodies, and over-limit
   expressions fail with a useful client error and execute no search SQL.
8. Extraction and `docker compose up --build` require no undocumented setup;
   README includes API URL, readiness, environment configuration, curl commands,
   test commands, and optional DBeaver connection instructions.
9. ZIP includes source, dependency specification, Dockerfile, Compose file,
   README, and tests; excludes secrets, caches, Git internals, local database
   files, and development worktrees.
10. Omitting controls yields limit 10, offset 0, ID ascending, and a 25,000 ms
    statement timeout. `limit=3, offset=3` returns the fourth through sixth
    matches in the specified stable order, or fewer if the result set ends.
11. On an unchanged dataset, consecutive pages contain no repeated or skipped
    matching IDs. Verify each allowed sort field in both directions, tied values,
    nulls last, empty datasets, offsets past the end, and sorting before pagination.
12. Unsafe sort fragments and all invalid controls fail with 422 and execute no
    search SQL. Malicious-looking leaf values remain literal data.
13. A statement exceeding the requested timeout returns 504, logs a safe correlated
    event, leaves no partial success response, and permits a subsequent search.
    Verify actual Postgres cancellation/connection cleanup, not only a mocked error.
    Very small client timeouts may intentionally fail the required query; the
    under-30-second performance requirement applies with defaults.
14. README provides a paginated custom-sort example, client timeout example,
    defaults/bounds, end-of-results behavior, and cross-page consistency limits.

## Open questions

No unresolved matching, pagination-style, sort-allowlist, or timeout-scope questions.
Detailed defaults, the 100,000 offset safety bound, partial-sort defaults,
text-order semantics, and bare-array page response were approved with v2.

## Approval decision

V1 was approved on 2026-11-04 with "LGTM". The user subsequently requested
pagination, custom sorting, and client-controlled timeouts, and explicitly chose
offset pagination, the four sort fields, and statement-only timeout semantics.
Before any implementation, `reopen prd` reset the PRD/downstream approvals.
The user approved PRD v2 and the revised architecture with "LGTM" after the
request to approve both documents. Record this confirmation in the control plane.
