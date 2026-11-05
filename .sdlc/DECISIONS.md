# Decision log

Keep entries short: what was decided, why, and affected epic/ticket.

## 2026-11-04 — Architecture review Q&A (pre-ticket)

Scope: PRD approved; architecture proposed; no epics or tickets exist yet.
Recorded here directly because the SDLC `decision` command requires an existing
epic/ticket reference. Questions are from the user; answers below distinguish
confirmed product choices from technical recommendations awaiting gate approval.

### 1. "why 3.12 instead of 3.14?"

Answer: 3.12 was a conservative default, not a measured compatibility need.
Recommend standard CPython 3.14. Python's release table lists 3.14 in bugfix
maintenance while 3.12 is security-only. FastAPI added Python 3.14 support in
0.118.3; current Psycopg supports Python 3.10–3.15. Use Pydantic v2 and verify
all exact dependency/tool pins in the 3.14 Docker image before implementation.
Do not claim a performance gain from changing Python without measurements.

Status: technical recommendation reflected in proposed ARCHITECTURE.md;
full architecture gate is still pending.

Sources:
- https://www.python.org/downloads/
- https://fastapi.tiangolo.com/release-notes/#01183
- https://www.psycopg.org/psycopg3/docs/basic/install.html

### 2. "Repo tree looks good"

Answer: Record the user's acceptance of the proposed tree. Add a small
observability module and concurrency/logging tests in the revised proposal
to support the follow-up questions; no broad layering or services are added.

Status: original tree accepted by user; the small additions and remaining
architecture decisions await the combined architecture approval. Do not call
`approve structure` on the basis of tree acceptance alone.

### 3. "How are we considering for performance? Concurrency mins and max for async functions?"

Answer: async does not imply a useful concurrency bound or make SQL faster.
Keep HTTP asynchronous, and move blocking Psycopg calls to worker threads.
Recommend one Uvicorn worker, four admitted searches, no waiting search queue,
zero idle DB connections, and a separately bounded single readiness probe.
The default maximum is five active DB operations, including readiness. The
actual database max_connections setting is 100; it is not a safe throughput
target. Reject excess searches immediately with 503/server_busy and Retry-After: 1.
Make search capacity configurable from 1–16. Never release a slot while its
worker/SQL still runs after request cancellation.

Bound connection setup to 3 seconds and search SQL to 25 seconds; add a 5-second
body read deadline and 2-second readiness SQL timeout. Existing expression/body
bounds and selecting IDs before hydration also constrain resource usage.
LIMIT does not guarantee cheap evaluation: set operations may scan whole tables.
The discovery query took 255.470 ms; it was SQL timing, not an API benchmark.
Measure the real API at concurrency 1 and 4, including percentiles, throughput,
overload, health responsiveness, failure recovery, and connection/slot cleanup.
The required Apple AND .net request must still meet the under-30-second bound.

Status: proposed resource defaults, not measured production capacity or a
guaranteed requests-per-second target. Exact behavior documented in architecture.

Source: https://anyio.readthedocs.io/en/stable/threads.html

### 4. "Should we include any optional parameters that the user may want to include?"

Answer: recommend optional `limit`, default 10, integer 1–10. It lets users ask
for fewer results without exceeding the approved maximum. Reject bool, float,
string, null, zero, negative, and values greater than 10 with 422. Bind the
limit in SQL. Keep pagination, client-selected timeouts, sorting, and field
projection out of scope; these add behavior beyond the assignment and approved
PRD. Operators already support selecting multiple organization/technology names.

Status: CONFIRMED by the user through the clarification answer:
"Add optional limit, 1–10 (recommended)." Incorporated in the architecture.
This preserves the approved PRD's fixed maximum of 10 and does not introduce
pagination. No approved document has been silently edited or reopened.

### 5. "How do protect against any potential SQL injections, or is that impossible/out of scope?"

Answer: injection prevention is required and was already in the approved PRD.
Use Psycopg parameter binding for every value and limit. Allow only known
fields/operators that map to predefined SQL fragments; no user-provided SQL
identifiers, fragments, or value interpolation. Compile parentheses from the
validated expression tree. An input such as `apple' OR 1=1 --` must be handled
as a literal name, not executable SQL. Verify this with actual database tests.
Do not rely on manual escaping. Read-only transactions and query limits are
additional defenses; the supplied postgres superuser is not least privilege.
A production SELECT-only role is a future deployment improvement, not required
to mutate the supplied database in this assignment.

Status: existing approved requirement clarified with concrete mechanisms;
implementation and verification remain future ticket work.

Source: https://www.psycopg.org/psycopg3/docs/basic/params.html

### 6. "Can we save/store/log errors (non 200 cases)"

Answer: yes. Propose structured JSON events for every completed non-200 response,
including framework errors and overload. Include UTC time, generated request ID,
method, normalized route, status, safe error code, and elapsed time. Return
the request ID for correlation. Track aborted requests separately when no
response is deliverable. Do not log bodies, query values, credentials, SQL
parameters, or raw driver messages. Unexpected errors retain exception class
and safe stack-frame locations without locals or raw exception text.

Write to stdout and rotating errors.jsonl in an app_logs Docker named volume.
Use 5 MiB files with three backups (about 20 MiB retained), plus bounded Docker
stdout rotation. Logs survive application container recreation and ordinary
Compose down; explicit volume deletion or rotation removes them. Log storage
must work when the database is unavailable, so do not store error events in
the query database. Document read/delete commands and verify rotation,
redaction, persistence, and non-200 coverage. This is recent operational
history rather than indefinite audit storage.

Status: user requested error storage; concrete storage/retention design proposed
for the architecture gate. No runtime logging has been implemented yet.

Source: https://docs.docker.com/engine/logging/drivers/json-file/

### Gate and change-control status

The approved PRD remains unchanged. The unapproved architecture is updated for
these answers, including confirmed optional limit and proposed concurrency and
error logging details. No skeleton, ticket, or implementation has started.
Next: obtain explicit approval of the revised architecture before scaffolding.

## 2026-11-04 — CR-001: pagination, custom sorting, client timeout

Stage: pre-implementation; no epics/tickets exist. This entry supersedes the
scope exclusions in Q&A 4 above and the earlier selected-ID-before-hydration
optimization when sorting by joined descriptive fields.

User request: "Lets add paginination, custome sorting, and client-controlled
timeouts, but ensure we have default values set for them."

Change-management action: preserved `.sdlc/revisions/PRD-v1-approved.md` and
`.sdlc/revisions/ARCHITECTURE-v1-proposed.md`, then ran `reopen prd` before
editing the approved PRD. Workflow is now phase prd, approvals reset. No ticket
history was discarded and no code or skeleton was started.

### Confirmed choices

- Pagination: user chose "Offset pagination (recommended)" with offset default 0
  and limit default/max 10. Document deep-offset costs and shifting pages when
  source data changes.
- Sorting: user chose id, datetime_pulled, job_title, and organization_name;
  one field with asc/desc, nulls last, default id ascending, ID tie-breaker.
- Timeout: user chose database statement only; timeout_ms default 25,000,
  integer 1–25,000. Connection setup and HTTP transfer are separate.

### Proposed detailed contract in PRD/architecture v2

- Request members: query (required), limit, offset, sort {field, direction},
  timeout_ms. Controls are optional; query-only requests keep working.
- Preserve limit 1–10, default 10. Bound offset to integer 0–100,000 as a proposed
  safety limit; return [] when the requested offset is beyond the matching set.
- Missing sort object defaults to id/asc; missing members within a present object
  independently default to id or asc. Reject explicit null and unknown members.
- Use ID ascending for non-ID ties, nulls last in both directions, and database
  collation on raw text for text sorts. Sorting must precede offset/limit.
- Keep a bare JSON array, no total count/cursor metadata. Advance offset by limit;
  a short page signals the end, while a full final page can require an empty
  follow-up request. No cross-request snapshot guarantee.
- Strict integer validation rejects bool, float, string, null, and bounds errors
  with 422 before search SQL. Sort keys/directions map to fixed SQL fragments;
  all values including pagination controls and timeout configuration are bound.
- Set the timeout transaction-locally; reject zero, which would disable the
  database timeout. Return 504 without partial results; retain capacity until
  database work stops and cleanup finishes. Default required-query timing stays
  under 30 seconds; very small client timeouts may intentionally produce 504.

### Impact and verification

Sort joined descriptive fields before limiting matches; selecting the first
ten IDs and sorting those would be incorrect. Expect more join/sort work,
especially at deep offsets; do not reuse the earlier 255 ms probe as evidence
of the revised implementation's performance. Test all sort keys/directions,
ties/nulls, multi-page continuity, invalid controls, injection attempts, actual
Postgres timeout cancellation/cleanup, and a successful subsequent request.
Benchmark real-image searches at default options, custom sorts, and deep offsets.
Update README examples/defaults and retain non-200 error logging.

Status: scope expansion and three major choices confirmed by the user. Detailed
v2 PRD and architecture remain DRAFT/PROPOSED pending approval; do not self-approve.
The original repo tree remains appropriate; no additional modules are needed.

References:
- https://www.postgresql.org/docs/current/queries-limit.html
- https://www.postgresql.org/docs/current/queries-order.html
- https://www.postgresql.org/docs/current/runtime-config-client.html

## Skeleton preparation after v2 approval

The user replied "LGTM" to the request to approve PRD v2 and revised architecture.
Both documents were finalized and `approve prd` then `approve structure` succeeded.
Phase is skeleton; skeleton approval is still pending. No implementation tickets
have been created or dispatched.

Created 24 approved-tree scaffold files using `scaffold`, with public contract
docstrings and explicit NotImplementedError stubs. The standard test command is
`python -m pytest -q`. Installed pinned runtime and quality dependency closures
successfully on CPython 3.14.10/Linux amd64, then observed a successful Docker
build, Compose configuration validation, Ruff formatting/lint, ty, and one
import-safety test. These checks do not verify API behavior or performance.

Added explicit .gitignore exceptions for .sdlc and .dockerignore so approved
workflow records and build exclusions can be committed before ticket work;
handoffs, runner logs, lock files, and runtime logs remain excluded.
See SKELETON_REVIEW.md for the reviewable tree, public signatures, and limitations.

## Skeleton review — code-flow diagram request

User requested a diagram of the path through the codebase. Added docs/code-flow.md
with a Mermaid request-flow diagram, module responsibilities, error/cleanup
branches, startup/health paths, and offline packaging path; linked from README
and SKELETON_REVIEW. It explicitly describes intended behavior because functions
are still stubs. Documentation-only addition; approved PRD/architecture unchanged.
Update the pending skeleton manifest to include the requested document and links.
The diagram request does not constitute skeleton approval.

### Diagram boundary clarification

User requested ingress/input, egress/output, and a final node to avoid an
apparent infinite loop. Updated docs/code-flow.md with a start, labeled HTTP/JSON
input, a shared HTTP/JSON response output, and terminal request-complete node.
Removed response arrows back to the request/client input. Both successful and
error responses terminate through the same egress. Documentation only; no
architecture or approval changes.

## Skeleton approved; planning authorized

The user replied "LGTM" after reviewing the revised ingress/egress/final-node
diagram and skeleton. Recorded `approve skeleton`; workflow is phase planning.
The approved scaffold and persisted control documents are to be committed to
main before ticket execution. Implementation and board approval remain pending.

