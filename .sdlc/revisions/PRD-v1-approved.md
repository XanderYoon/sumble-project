# Advanced queries on Sumble — product requirements

Status: FINAL — approved by the user on 2026-11-04 ("LGTM").

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
5. Return each matching job once, with a fixed maximum of 10 jobs per response.
   Use a stable order based on a unique job identifier.
6. Read from database image `nzsne8pttnh8wrpu/p2ckp9ddret2arc2`, database
   `sumble_data`. Discover actual tables, relationships, and identifier types
   before specifying SQL and the final response schema.
7. Include both required curl examples and a nested AND/OR example in the README.

## Nonfunctional requirements

- Python implementation, Dockerfile, and `docker-compose.yml`; a fresh
  `docker compose up --build` starts the supplied database and API, with
  database readiness handled before search requests are served.
- The required Apple AND .net request must complete in less than 30 seconds
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
  invalid requests, SQL injection resistance, and real-dataset execution.
- Local evaluation scope; public hosting and production authentication are excluded.

## Interfaces, inputs, outputs, and error behavior

Proposed endpoint: `POST /jobs/search` with `Content-Type: application/json`.

The request has a required `query` member containing exactly one expression:

```json
{"query":{"and":[{"field":"organization","value":"apple"},{"field":"technology","value":".net"}]}}
```

Expression forms:

- Leaf: `{"field":"technology","value":"python"}` (nonempty string).
- AND: `{"and":[expression, expression]}` (one or more children).
- OR: `{"or":[expression, expression]}` (one or more children).
- NOT: `{"not":expression}` (exactly one child).

Reject unknown fields, extra properties, ambiguous nodes, empty AND/OR arrays,
non-string values, malformed JSON, and expressions exceeding documented limits.
Return a documented client-error response; exact status mapping and error shape
will be specified in the architecture. Do not silently ignore invalid inputs.

Success returns HTTP 200 and a JSON array of job objects, or `[]` for no matches.
The actual job fields will be documented after schema inspection; include the
job identifier and available descriptive job data. Database unavailability or
timeout returns a documented non-success status, never an empty successful list.

Required second example:

```json
{"query":{"and":[{"not":{"field":"organization","value":"apple"}},{"or":[{"field":"job_function","value":"statistician"},{"field":"technology","value":"psql"}]}]}}
```

## Out of scope

String-query parsing, simple-filter UI, frontend development, user accounts,
pagination, total counts, ranking, fuzzy matching, database content editing,
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

## Open questions

No unresolved matching questions. API contract, fixed 10-job cap,
ordering, and validation policy were approved with this PRD.
Database schema and physical query plan are technical discovery work for
the architecture stage.

## Approval decision

The user approved this PRD on 2026-11-04 with "LGTM" after reviewing the draft.
