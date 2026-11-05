"""Bounded HTTP input, safe responses, and database-independent request logging."""

import asyncio
import json
import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic
from typing import NoReturn

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sumble_api import database, observability, query
from sumble_api.models import ErrorDetail, ErrorResponse, Job

BODY_READ_SECONDS = 5
_DESCRIPTION = """
Search exact names case-insensitively using a required `query` expression in UTF-8 JSON.
Every response includes a server-generated X-Request-ID; errors repeat it in
error.request_id for correlation with safe structured logs.
Leaf: `{"field":"technology","value":"python"}`. Fields: technology,
job_function, organization. AND/OR: `{"and":[expression,...]}` or
`{"or":[expression,...]}` with at least one child. NOT: `{"not":expression}`.
Unknown members, ambiguous nodes, null controls, and blank values are rejected.
Limits: 16384 actual body bytes, 5 seconds to read the body, 128 expression nodes,
16 expression levels (root is 1), 256 characters per leaf value. JSON nesting
beyond 64 containers is rejected as malformed input (400).

Optional integer controls: limit 1-10 (default 10), offset 0-100000 (default 0),
timeout_ms 1-25000 (default 25000; database statement only, not an HTTP deadline).
Booleans, floats, and numeric strings are rejected. Optional sort accepts field
(id, datetime_pulled, job_title, organization_name; default id) and direction
(asc or desc; default asc). Either sort member may be omitted. Nulls sort last;
non-ID ties use ID ascending. Text order follows database collation.

Returns a bare job array. Advance offset by limit; fewer than limit results
signals the end. A full final page may require a further empty page. Concurrent
data changes can shift pages; no snapshot spans requests. Missing associations
fail positive leaves and satisfy their negations. Values are literal exact names,
not partial or fuzzy matches, and are not trimmed.
"""
_ERRORS = {
    400: ("invalid_json", "Request body must contain valid JSON."),
    408: ("request_timeout", "Request body read deadline exceeded."),
    413: ("body_too_large", "Request body exceeds 16384 bytes."),
    415: ("unsupported_media_type", "Content-Type must be application/json."),
    422: ("invalid_request", "Request validation failed."),
    503: ("database_unavailable", "Database is unavailable."),
    504: ("statement_timeout", "Search statement timed out."),
    500: ("internal_error", "Internal server error."),
    404: ("not_found", "Route not found."),
    405: ("method_not_allowed", "Method not allowed."),
}


class PublicError(Exception):
    """Carry only server-selected public error information."""

    def __init__(self, status: int, message: str | None = None, path: str | None = None) -> None:
        """Select a fixed error code and optional validator-owned explanation."""
        self.status = status
        self.code, default_message = _ERRORS[status]
        self.message = message or default_message
        self.path = path


class ServerBusyError(PublicError):
    """Report exhausted search or readiness capacity without a waiting queue."""

    def __init__(self) -> None:
        """Select the fixed overload response independently of database failures."""
        super().__init__(503, "Server is busy. Try again shortly.")
        self.code = "server_busy"


def _error_response(scope: Scope, error: PublicError) -> JSONResponse:
    scope.setdefault("state", {})["error_code"] = error.code
    body = ErrorResponse(
        error=ErrorDetail(
            code=error.code,
            message=error.message,
            request_id=observability.current_request_id() or "",
            path=error.path,
        )
    )
    headers = {"Retry-After": "1"} if isinstance(error, ServerBusyError) else None
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=error.status, headers=headers
    )


class RequestMiddleware:
    """Own correlation and catch exceptions before framework traceback logging."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the ASGI application without buffering request or response bodies."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record completed responses and separately record aborted delivery."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = monotonic()
        status: int | None = None
        completed = False
        diagnostic: BaseException | None = None
        with observability.request_context() as request_id:

            async def tracked_send(message: Message) -> None:
                nonlocal status, completed
                if message["type"] == "http.response.start":
                    status = message["status"]
                    message["headers"] = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"x-request-id"
                    ] + [(b"x-request-id", request_id.encode("ascii"))]
                await send(message)
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    completed = True

            try:
                try:
                    await self.app(scope, receive, tracked_send)
                except PublicError as error:
                    await _error_response(scope, error)(scope, receive, tracked_send)
                except ClientDisconnect:
                    pass  # No transport remains on which to deliver an error response.
                except Exception as error:
                    diagnostic = error
                    if status is None:
                        await _error_response(scope, PublicError(500))(scope, receive, tracked_send)
            finally:
                route = scope.get("route")
                observability.log_request(
                    method=scope.get("method", ""),
                    route=getattr(route, "path", None),
                    status=status if completed else None,
                    code=scope.get("state", {}).get("error_code", "ok")
                    if completed
                    else "request_aborted",
                    duration_ms=(monotonic() - started) * 1000,
                    error=diagnostic,
                )


async def _read_payload(request: Request) -> object:
    """Bound actual streamed bytes and the total read deadline before JSON parsing."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise PublicError(415)
    body = bytearray()
    try:
        with anyio.fail_after(BODY_READ_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > query.MAX_BODY_BYTES:
                    raise PublicError(413)
                body.extend(chunk)
    except TimeoutError:
        raise PublicError(408) from None
    try:
        text = body.decode("utf-8")
        _check_json_depth(body)
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError, UnicodeError, RecursionError:
        raise PublicError(400) from None


def _check_json_depth(body: bytearray) -> None:
    # Scan structural ASCII bytes only; brackets inside escaped strings are data.
    depth = 0
    quoted = False
    escaped = False
    for value in body:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            if depth > 64:
                raise PublicError(400)
        elif value in (93, 125):
            depth -= 1


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Nonstandard JSON constant.")


def _search_capacity() -> int:
    value = os.environ.get("MAX_CONCURRENT_SEARCHES", "4")
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 16:
        raise ValueError("MAX_CONCURRENT_SEARCHES must be an integer from 1 to 16.")
    return int(value)


async def _run_worker[T](operation: Callable[[], T]) -> T:
    """Finish synchronous work and cleanup before propagating cancellation.

    AnyIO shielding covers cancel scopes. The separate asyncio task also protects
    against direct ASGI task cancellation, which bypasses AnyIO's shield. Repeated
    cancellation must never release admission while the worker is still running.
    """
    with anyio.CancelScope(shield=True):
        worker = asyncio.create_task(anyio.to_thread.run_sync(operation, abandon_on_cancel=False))
        cancellation: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                cancellation = error
            except Exception:
                if cancellation is None:
                    raise
                # Once the transport is cancelled, preserve that outcome even if work fails.
        if cancellation is not None:
            # Retrieve any worker exception, but preserve the transport cancellation.
            try:
                worker.result()
            finally:
                raise cancellation from None
    return worker.result()


def create_app() -> FastAPI:
    """Construct routes and logging without opening database connections.

    Returns:
        An application with bounded input and correlated, safe error responses.
    """
    searches = BoundedSemaphore(_search_capacity())
    readiness = BoundedSemaphore(1)
    observability.configure_logging(Path(os.environ.get("LOG_DIR", "/var/log/sumble")))
    app = FastAPI(title="Sumble Boolean Job Search", description=_DESCRIPTION)
    app.add_middleware(RequestMiddleware)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        status = error.status_code
        public = PublicError(status) if status in _ERRORS else PublicError(500)
        response = _error_response(request.scope, public)
        if status == 405 and error.headers and "Allow" in error.headers:
            response.headers["Allow"] = error.headers["Allow"]
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response(request.scope, PublicError(422))

    @app.post(
        "/jobs/search",
        response_model=list[Job],
        description=_DESCRIPTION,
        responses={
            status: {"model": ErrorResponse, "description": message}
            for status, (_, message) in _ERRORS.items()
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["query"],
                            "additionalProperties": False,
                            "properties": {
                                "query": {
                                    "type": "object",
                                    "description": "Boolean expression as described above.",
                                },
                                "limit": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": query.MAX_LIMIT,
                                    "default": query.DEFAULT_LIMIT,
                                },
                                "offset": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": query.MAX_OFFSET,
                                    "default": 0,
                                },
                                "timeout_ms": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": query.MAX_TIMEOUT_MS,
                                    "default": query.DEFAULT_TIMEOUT_MS,
                                },
                                "sort": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "field": {
                                            "type": "string",
                                            "enum": [
                                                "id",
                                                "datetime_pulled",
                                                "job_title",
                                                "organization_name",
                                            ],
                                            "default": "id",
                                        },
                                        "direction": {
                                            "type": "string",
                                            "enum": ["asc", "desc"],
                                            "default": "asc",
                                        },
                                    },
                                },
                            },
                        },
                        "examples": {
                            "apple_dotnet": {
                                "value": {
                                    "query": {
                                        "and": [
                                            {"field": "organization", "value": "apple"},
                                            {"field": "technology", "value": ".net"},
                                        ]
                                    }
                                }
                            },
                            "not_apple": {
                                "value": {
                                    "query": {
                                        "and": [
                                            {"not": {"field": "organization", "value": "apple"}},
                                            {
                                                "or": [
                                                    {
                                                        "field": "job_function",
                                                        "value": "statistician",
                                                    },
                                                    {"field": "technology", "value": "psql"},
                                                ]
                                            },
                                        ]
                                    }
                                }
                            },
                            "controls": {
                                "value": {
                                    "query": {"field": "technology", "value": "python"},
                                    "limit": 3,
                                    "offset": 3,
                                    "sort": {"field": "job_title", "direction": "desc"},
                                    "timeout_ms": 1000,
                                }
                            },
                        },
                    }
                },
            }
        },
    )
    async def search(request: Request) -> list[Job]:
        if not searches.acquire(blocking=False):
            raise ServerBusyError
        try:
            try:
                validated = query.parse_query(await _read_payload(request))
            except query.QueryValidationError as error:
                raise PublicError(422, error.message, error.path) from None
            try:
                return await _run_worker(partial(database.search_jobs, validated))
            except database.SearchTimeoutError:
                raise PublicError(504) from None
            except database.DatabaseUnavailableError:
                raise PublicError(503) from None
        finally:
            searches.release()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", responses={503: {"model": ErrorResponse}})
    async def ready() -> dict[str, str]:
        if not readiness.acquire(blocking=False):
            raise ServerBusyError
        try:
            try:
                await _run_worker(database.check_database)
            except database.DatabaseUnavailableError:
                raise PublicError(503) from None
            return {"status": "ready"}
        finally:
            readiness.release()

    return app
