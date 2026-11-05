"""Core safe-logging tests.

Coverage gaps:
- Status-to-level mapping for 2xx/3xx/4xx/5xx and aborted requests
- Duration clamping (NaN, inf, negative, huge)
- Exception frame cap of 8 without source, locals, or function names
- Concurrent request_id isolation and nested context reset
- Real 5 MiB x 3 backup rotation and reconfiguration without duplicate sinks
- File-write failure fallback without raw tracebacks
- Rotation rename failure redacts filesystem paths
- uvicorn error/access loggers disabled and app logger does not propagate
"""

import json
import logging

import pytest

from sumble_api import observability


@pytest.fixture(autouse=True)
def isolated_logging():
    yield
    logger = logging.getLogger(observability.LOGGER_NAME)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def emit(*, status=422, error=None):
    return observability.log_request(
        method="POST",
        route="/jobs/search",
        status=status,
        code="invalid_request",
        duration_ms=1.0,
        error=error,
    )


def test_untrusted_exception_details_are_redacted(tmp_path, capsys):
    observability.configure_logging(tmp_path)
    secret = "password=hunter2"
    try:
        raise ValueError(secret)
    except ValueError as error:
        emit(error=error)
    output = capsys.readouterr().out
    event = json.loads(output)
    assert secret not in output
    assert event["exception"]["class"] == "ValueError"
    assert json.loads((tmp_path / "errors.jsonl").read_text()) == event


def test_setup_failure_keeps_stdout_and_successful_requests(tmp_path, capsys):
    blocked = tmp_path / "password-secret"
    blocked.write_text("not a directory", encoding="utf-8")
    observability.configure_logging(blocked)
    emit(status=200)
    emit(status=503)
    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines()]
    assert events[0]["event"] == "logging_failure"
    assert [event["status"] for event in events[1:]] == [200, 503]
    assert "password-secret" not in output
