"""Behavior checks for safe database-independent logging."""

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from uuid import UUID

import pytest

from sumble_api import observability


@pytest.fixture(autouse=True)
def isolated_logging():
    previous = {
        name: logging.getLogger(name).disabled for name in ("uvicorn.error", "uvicorn.access")
    }
    yield
    logger = logging.getLogger(observability.LOGGER_NAME)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    for name, disabled in previous.items():
        logging.getLogger(name).disabled = disabled


def emit(
    *,
    status=422,
    method="POST",
    route="/jobs/search",
    code="invalid_request",
    duration_ms=1.23456,
    error=None,
):
    return observability.log_request(
        method=method, route=route, status=status, code=code, duration_ms=duration_ms, error=error
    )


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_status_levels_stdout_and_non_200_file(tmp_path, capsys):
    observability.configure_logging(tmp_path)
    for status in (200, 201, 307, 400, 404, 422, 500, 503, None):
        emit(status=status)
    stdout = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["level"] for event in stdout] == [
        "INFO",
        "INFO",
        "INFO",
        "WARNING",
        "WARNING",
        "WARNING",
        "ERROR",
        "ERROR",
        "ERROR",
    ]
    assert read_events(tmp_path / "errors.jsonl") == stdout[1:]
    assert stdout[-1]["event"] == "request_aborted"
    assert all(event["timestamp"].endswith("Z") for event in stdout)
    assert all(UUID(event["request_id"]).version == 4 for event in stdout)
    assert all(event["duration_ms"] == 1.235 for event in stdout)


@pytest.mark.parametrize(
    "secret",
    [
        'password=hunter2\n{"level":"INFO","forged":true}',
        "Bearer abc.def.ghi",
        "postgresql://user:password@localhost/db",
        "SELECT * FROM jobs WHERE value='private'",
        "\x00\r\n\u2028秘密",
        "a" * 100000,
    ],
)
def test_untrusted_fields_and_exception_details_are_redacted(tmp_path, capsys, secret):
    observability.configure_logging(tmp_path)
    # Raw messages, locals, notes, causes, custom class names and filenames are unsafe.
    error_type = type(secret.replace("\x00", ""), (Exception,), {})
    try:
        raise error_type(secret) from ValueError(secret)
    except Exception as error:
        error.add_note(secret)
        emit(method=secret, route=secret, code=secret, error=error)
    output = capsys.readouterr().out
    event = json.loads(output)
    assert len(output.splitlines()) == 1
    assert len(output) < 1000
    assert secret not in output
    assert event["method"] == "OTHER"
    assert event["route"] == "<unmatched>"
    assert event["code"] == "internal_error"
    assert event["exception"] == {
        "class": "Exception",
        "frames": [{"file": "<external>", "line": event["exception"]["frames"][0]["line"]}],
    }
    assert read_events(tmp_path / "errors.jsonl") == [event]


@pytest.mark.parametrize(
    "duration, expected", [(float("nan"), 0), (float("inf"), 0), (-10, 0), (1e99, 86400000)]
)
def test_duration_bounds(tmp_path, capsys, duration, expected):
    observability.configure_logging(tmp_path)
    emit(duration_ms=duration)
    assert json.loads(capsys.readouterr().out)["duration_ms"] == expected


def test_bounded_frames_without_source_or_locals(tmp_path, capsys):
    observability.configure_logging(tmp_path)

    def recurse(depth):
        secret = "do-not-log-this-local"
        if depth:
            return recurse(depth - 1)
        raise ValueError(secret)

    try:
        recurse(20)
    except ValueError as error:
        emit(error=error)
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["exception"]["class"] == "ValueError"
    assert len(event["exception"]["frames"]) == 8
    assert "do-not-log" not in output
    assert "recurse" not in output


def test_request_ids_are_isolated_and_reset(tmp_path, capsys):
    observability.configure_logging(tmp_path)

    async def request():
        with observability.request_context() as request_id:
            await asyncio.sleep(0)
            assert observability.current_request_id() == request_id
            assert emit() == request_id
            return request_id

    async def run():
        return await asyncio.gather(*(request() for _ in range(12)))

    ids = asyncio.run(run())
    assert len(set(ids)) == 12
    assert observability.current_request_id() is None
    with observability.request_context() as outer:
        with pytest.raises(RuntimeError), observability.request_context() as inner:
            assert inner != outer
            raise RuntimeError("private")
        assert observability.current_request_id() == outer
    assert observability.current_request_id() is None
    assert {event["request_id"] for event in read_events(tmp_path / "errors.jsonl")} == set(ids)
    capsys.readouterr()


def test_real_rotation_three_backups_and_reconfiguration(tmp_path, capsys):
    observability.configure_logging(tmp_path, max_bytes=800)
    observability.configure_logging(tmp_path, max_bytes=800)
    for _ in range(40):
        emit()
    output = capsys.readouterr().out.splitlines()
    assert len(output) == 40  # Repeated setup must not duplicate either sink.
    files = sorted(tmp_path.iterdir())
    assert [path.name for path in files] == [
        "errors.jsonl",
        "errors.jsonl.1",
        "errors.jsonl.2",
        "errors.jsonl.3",
    ]
    persisted = [event for path in files for event in read_events(path)]
    assert 4 <= len(persisted) < 40
    assert all(path.stat().st_size < 800 for path in files)
    assert len({event["request_id"] for event in persisted}) == len(persisted)
    observability.configure_logging(tmp_path, max_bytes=800)
    assert read_events(files[0])  # Setup preserves existing history.


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


def test_write_failure_falls_back_without_raw_traceback(tmp_path, capsys):
    observability.configure_logging(tmp_path)
    file_handler = next(
        handler
        for handler in logging.getLogger(observability.LOGGER_NAME).handlers
        if isinstance(handler, RotatingFileHandler)
    )
    file_handler.close()
    path = tmp_path / "errors.jsonl"
    path.unlink()
    path.mkdir()  # A real filesystem failure when the handler next opens its sink.
    emit(status=503)
    emit(status=200)
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert [event["event"] for event in events] == [
        "request_completed",
        "logging_failure",
        "request_completed",
    ]
    assert events[0]["status"] == 503
    assert events[-1]["status"] == 200
    assert not captured.err


def test_framework_tracebacks_disabled(tmp_path):
    observability.configure_logging(tmp_path)
    assert logging.getLogger("uvicorn.error").disabled
    assert logging.getLogger("uvicorn.access").disabled
    assert not logging.getLogger(observability.LOGGER_NAME).propagate


def test_default_rotation_contract(tmp_path):
    observability.configure_logging(tmp_path)
    file_handler = next(
        handler
        for handler in logging.getLogger(observability.LOGGER_NAME).handlers
        if isinstance(handler, RotatingFileHandler)
    )
    assert file_handler.maxBytes == 5 * 1024 * 1024
    assert file_handler.backupCount == 3


def test_rotation_failure_is_safe_and_stdout_survives(tmp_path, capsys):
    observability.configure_logging(tmp_path, max_bytes=400)
    emit()
    # An occupied backup directory makes the actual rename operation fail.
    (tmp_path / "errors.jsonl.2").write_text("old log", encoding="utf-8")
    backup = tmp_path / "errors.jsonl.3"
    backup.mkdir()
    (backup / "private-password").write_text("secret", encoding="utf-8")
    emit()
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert [event["event"] for event in events] == [
        "request_completed",
        "request_completed",
        "logging_failure",
    ]
    assert "secret" not in captured.out
    assert "private-password" not in captured.out
    assert not captured.err
