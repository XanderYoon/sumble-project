"""Bounded HTTP input, safe responses, and request logging."""

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
    """Assign a server request ID, map public failures, and log one outcome."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Log the request after the response, including when delivery is aborted."""
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
                    pass
                except Exception as error:
                    diagnostic = error
                    if status is None:
                        await _error_response(scope, PublicError(500))(scope, receive, tracked_send)
            finally:
                route = scope.get("route")
                observability.log_request(
                    method=str(scope.get("method", "")),
                    route=getattr(route, "path", None),
                    status=status if completed else None,
                    code=scope.get("state", {}).get("error_code", "ok")
                    if completed
                    else "request_aborted",
                    duration_ms=(monotonic() - started) * 1000,
                    error=diagnostic,
                )


async def _read_payload(request: Request) -> object:
    """Bound the streamed body and decode it as UTF-8 JSON."""
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
        return json.loads(body, parse_constant=_reject_constant)
    except ValueError, UnicodeError, RecursionError:
        raise PublicError(400) from None


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("Nonstandard JSON constant.")


def _search_capacity() -> int:
    value = os.environ.get("MAX_CONCURRENT_SEARCHES", "4")
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 16:
        raise ValueError("MAX_CONCURRENT_SEARCHES must be an integer from 1 to 16.")
    return int(value)


async def _run_worker[T](operation: Callable[[], T]) -> T:
    """Run blocking work to completion before a cancelled request continues.

    ``abandon_on_cancel`` is disabled, so a disconnect keeps the admission slot
    until the worker thread returns. The slot is released by the route's finally.
    """
    return await anyio.to_thread.run_sync(operation, abandon_on_cancel=False)


def create_app() -> FastAPI:
    """Construct routes and logging without opening database connections.

    Returns:
        An application with bounded input and correlated, safe error responses.
    """
    searches = BoundedSemaphore(_search_capacity())
    readiness = BoundedSemaphore(1)
    observability.configure_logging(Path(os.environ.get("LOG_DIR", "/var/log/sumble")))
    app = FastAPI(
        title="Sumble Boolean Job Search",
        description="Boolean job search over the supplied dataset. The README is the request contract.",
    )
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

    @app.post("/jobs/search", response_model=list[Job])
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
