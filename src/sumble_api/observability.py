"""JSON request logs on stdout, with non-200 lines copied to a rotating file."""

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


@contextmanager
def request_context() -> Generator[str]:
    """Generate a server-owned ID and reset it after this request.

    Never seed this context from a client header.
    """
    request_id = uuid4().hex
    token = _REQUEST_ID.set(request_id)
    try:
        yield request_id
    finally:
        _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    """Return the active server-generated request ID, if inside a request."""
    return _REQUEST_ID.get()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _diagnostic() -> None:
    # A failed log path can itself contain a secret, so the diagnostic is fixed.
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
    except OSError:
        pass


def _non_success(record: logging.LogRecord) -> bool:
    return record.__dict__.get("safe_status") != 200


def configure_logging(log_dir: Path, *, max_bytes: int = MAX_BYTES) -> None:
    """Attach JSON stdout and a rotating non-200 file sink.

    Args:
        log_dir: Directory for errors.jsonl, normally /var/log/sumble.
        max_bytes: Rotation threshold. The deployment default is 5 MiB.

    File setup failure emits a fixed stdout diagnostic and does not fail startup.
    Reconfiguration closes prior sinks.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stdout)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "errors.jsonl",
            maxBytes=max_bytes,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        _diagnostic()
        return
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler.addFilter(_non_success)
    logger.addHandler(file_handler)


def log_request(
    *,
    method: str,
    route: str | None,
    status: int | None,
    code: str,
    duration_ms: float,
    error: BaseException | None = None,
) -> str:
    """Emit one request event and return its server-generated correlation ID.

    Call within request_context so the ID matches the response header. The event
    records the exception class only. Exception text, bodies, headers, and SQL
    are not accepted. A missing status records an aborted request.

    Args:
        method: HTTP method, or an empty string when the server has none.
        route: Matched route template, or None when no route matched.
        status: HTTP status, or None when no response was delivered.
        code: Application error code, or ``ok`` for a successful response.
        duration_ms: Elapsed request time in milliseconds.
        error: Unexpected failure, recorded by class name only.

    Returns:
        The request ID included in the event.
    """
    request_id = current_request_id() or uuid4().hex
    safe_status = status if type(status) is int and 100 <= status <= 599 else None
    if safe_status is None or safe_status >= 500:
        level = logging.ERROR
    elif safe_status >= 400:
        level = logging.WARNING
    else:
        level = logging.INFO
    duration = round(duration_ms, 3) if math.isfinite(duration_ms) else 0
    event: dict[str, object] = {
        "timestamp": _timestamp(),
        "level": logging.getLevelName(level),
        "event": "request_aborted" if safe_status is None else "request_completed",
        "request_id": request_id,
        "method": method or "OTHER",
        "route": route or "<unmatched>",
        "status": safe_status,
        "code": code,
        "duration_ms": duration,
    }
    if error is not None:
        event["exception"] = {"class": type(error).__name__}
    logging.getLogger(LOGGER_NAME).log(
        level,
        json.dumps(event, ensure_ascii=True, allow_nan=False),
        extra={"safe_status": safe_status},
    )
    return request_id
