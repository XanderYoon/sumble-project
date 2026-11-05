"""Bounded structured logging without request values or exception messages."""

import builtins
import json
import logging
import math
import sys
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

LOGGER_NAME = "sumble.safe"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_REQUEST_ID: ContextVar[str | None] = ContextVar("sumble_request_id", default=None)
_ROUTES = frozenset({"/jobs/search", "/health", "/ready", "/docs", "/redoc", "/openapi.json"})
_CODES = frozenset(
    {
        "ok",
        "http_error",
        "invalid_json",
        "request_timeout",
        "unsupported_media_type",
        "body_too_large",
        "invalid_request",
        "database_unavailable",
        "server_busy",
        "statement_timeout",
        "internal_error",
        "not_found",
        "method_not_allowed",
        "request_aborted",
    }
)
_APP_FILES = frozenset(
    {"app.py", "query.py", "compiler.py", "database.py", "models.py", "observability.py"}
)


@contextmanager
def request_context() -> Generator[str]:
    """Generate a server-owned ID and reset correlation after this request.

    Context variables isolate concurrent async requests and restore nested scopes.
    Never seed this context from a client header.
    """
    request_id = uuid4().hex
    token = _REQUEST_ID.set(request_id)
    try:
        yield request_id
    finally:
        _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    """Return the active server-generated request ID, if inside a request scope."""
    return _REQUEST_ID.get()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _diagnostic() -> None:
    # Never interpolate a path or an OSError: both may contain credentials.
    try:
        sys.stdout.write(
            json.dumps(
                {
                    "timestamp": _timestamp(),
                    "level": "ERROR",
                    "event": "logging_failure",
                    "code": "file_unavailable",
                }
            )
            + "\n"
        )
        sys.stdout.flush()
    except OSError, ValueError:
        pass  # Logging must not replace the application's original outcome.


class _NonSuccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.__dict__.get("safe_status") != 200


class _ResilientFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        _diagnostic()


class _ResilientStdoutHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # A broken stdout cannot safely report its own failure.
        pass


def configure_logging(log_dir: Path, *, max_bytes: int = MAX_BYTES) -> None:
    """Replace this module's sinks with JSON stdout and a rotating non-200 file.

    Args:
        log_dir: Directory for errors.jsonl, normally /var/log/sumble.
        max_bytes: Rotation threshold; the deployment default is 5 MiB.

    File setup/write failures emit a fixed diagnostic and never fail a request.
    Reconfiguration closes prior sinks, preventing duplicates and descriptor leaks.
    Uvicorn's raw access/error loggers are disabled to prevent traceback leaks.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    stdout = _ResilientStdoutHandler(sys.stdout)
    stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stdout)
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).disabled = True
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = _ResilientFileHandler(
            log_dir / "errors.jsonl",
            maxBytes=max_bytes,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        _diagnostic()
        return
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler.addFilter(_NonSuccess())
    logger.addHandler(file_handler)


def _safe_exception(error: BaseException) -> dict[str, object]:
    exception_type = type(error)
    name = exception_type.__name__
    # Custom class names and dynamically compiled filenames can contain secrets.
    safe_name = name if getattr(builtins, name, None) is exception_type else "Exception"
    frames: list[dict[str, object]] = []
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        path = Path(code.co_filename)
        app_frame = path.parent.name == "sumble_api" and path.name in _APP_FILES
        frames.append(
            {
                "file": path.name if app_frame else "<external>",
                "line": min(max(traceback.tb_lineno, 0), 999999),
            }
        )
        frames = frames[-8:]
        traceback = traceback.tb_next
    return {"class": safe_name, "frames": frames}


def log_request(
    *,
    method: str,
    route: str | None,
    status: int | None,
    code: str,
    duration_ms: float,
    error: BaseException | None = None,
) -> str:
    """Emit one safe request event and return its server-generated correlation ID.

    Call within request_context for correlation with response headers/bodies.
    Pass the matched route template, never a raw URL. Unrecognized routes/codes
    become fixed labels. None status records an aborted response at ERROR level.
    Bodies, headers, exception strings, SQL, and arbitrary extra fields are never
    accepted. Durations are finite and clamped to one day; frames are capped at 8.
    """
    request_id = current_request_id() or uuid4().hex
    safe_status = status if type(status) is int and 100 <= status <= 599 else None
    level = (
        logging.ERROR
        if safe_status is None or safe_status >= 500
        else (logging.WARNING if safe_status >= 400 else logging.INFO)
    )
    event: dict[str, object] = {
        "timestamp": _timestamp(),
        "level": logging.getLevelName(level),
        "event": "request_aborted" if safe_status is None else "request_completed",
        "request_id": request_id,
        "method": method
        if method
        in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
        else "OTHER",
        "route": route if route in _ROUTES else "<unmatched>",
        "status": safe_status,
        "code": code if code in _CODES else "internal_error",
        "duration_ms": round(min(max(duration_ms, 0), 86400000), 3)
        if math.isfinite(duration_ms)
        else 0,
    }
    if error is not None:
        event["exception"] = _safe_exception(error)
    logging.getLogger(LOGGER_NAME).log(
        level,
        json.dumps(event, ensure_ascii=True, allow_nan=False),
        extra={"safe_status": safe_status},
    )
    return request_id
