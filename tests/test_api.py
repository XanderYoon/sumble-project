"""HTTP boundary contracts without a live database."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient

from sumble_api import app, database
from sumble_api.models import Job

LEAF = {"query": {"field": "organization", "value": "apple"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    with TestClient(app.create_app()) as client:
        yield client


def test_importing_modules_does_not_connect_to_database():
    code = """
import importlib
import psycopg

def unexpected(*args, **kwargs):
    raise AssertionError('Import must not connect')

psycopg.connect = unexpected
for name in ('app', 'query', 'compiler', 'database', 'models', 'observability'):
    importlib.import_module('sumble_api.' + name)
"""
    subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": "src"},
        check=True,
        capture_output=True,
    )


def test_query_and_controlled_search_are_bare_typed_arrays(client, monkeypatch):
    calls = []

    def search(request):
        calls.append(request)
        return [Job(id=12, organization_name="Apple")]

    monkeypatch.setattr(database, "search_jobs", search)
    first = client.post("/jobs/search", json=LEAF, headers={"X-Request-ID": "client-secret"})
    second = client.post(
        "/jobs/search",
        json={**LEAF, "limit": 3, "offset": 3, "sort": {"direction": "desc"}, "timeout_ms": 1},
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == [Job(id=12, organization_name="Apple").model_dump(mode="json")]
    assert UUID(first.headers["x-request-id"]).version == 4
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert calls[0].options.limit == 10
    assert calls[1].options.limit == 3
    assert calls[1].options.sort_field == "id"
    assert calls[1].options.sort_direction == "desc"
    monkeypatch.setattr(database, "search_jobs", lambda _: [])
    assert client.post("/jobs/search", json=LEAF).json() == []


@pytest.mark.parametrize(
    "body, media_type, status, code",
    [
        (b"{", "application/json", 400, "invalid_json"),
        (b"\xff", "application/json", 400, "invalid_json"),
        (b"NaN", "application/json", 400, "invalid_json"),
        (b"[" * 1500 + b"]" * 1500, "application/json", 400, "invalid_json"),
        (b"x" * 16385, "application/json", 413, "body_too_large"),
        (b"{}", "text/plain", 415, "unsupported_media_type"),
        (b"{}", "", 415, "unsupported_media_type"),
        (b'{"secret-key":"private"}', "application/json", 422, "invalid_request"),
        (json.dumps({**LEAF, "limit": True}).encode(), "application/json", 422, "invalid_request"),
    ],
)
def test_invalid_input_is_safe_and_never_searches(
    client, monkeypatch, body, media_type, status, code
):
    def unexpected(_):
        raise AssertionError("Validation must precede SQL")

    monkeypatch.setattr(database, "search_jobs", unexpected)
    response = client.post("/jobs/search", content=body, headers={"Content-Type": media_type})
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    assert "private" not in response.text
    assert "secret-key" not in response.text


@pytest.mark.parametrize(
    "error, status, code",
    [
        (database.DatabaseUnavailableError("password-secret"), 503, "database_unavailable"),
        (database.SearchTimeoutError("SELECT private"), 504, "statement_timeout"),
        (ValueError("postgres://credential-secret"), 500, "internal_error"),
    ],
)
def test_search_errors_are_safe_and_correlated(
    client, monkeypatch, tmp_path, capsys, error, status, code
):
    def fail(_):
        raise error

    monkeypatch.setattr(database, "search_jobs", fail)
    response = client.post("/jobs/search", json=LEAF)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    events = [json.loads(line) for line in (tmp_path / "errors.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["request_id"] == response.headers["x-request-id"]
    assert events[0]["code"] == code
    captured = capsys.readouterr()
    assert str(error) not in response.text + captured.out + captured.err
    assert not captured.err


def test_health_readiness_framework_errors_and_docs(client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "check_database", lambda: None)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200

    def unavailable():
        raise database.DatabaseUnavailableError("password-secret")

    monkeypatch.setattr(database, "check_database", unavailable)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    missing = client.get("/private-password-secret")
    method = client.put("/jobs/search")
    assert missing.status_code == 404
    assert method.status_code == 405
    assert "POST" in method.headers["allow"]
    assert "private-password" not in missing.text
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/jobs/search"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "array"
    assert {"400", "408", "413", "415", "422", "500", "503", "504"} <= set(operation["responses"])
    assert "128 expression nodes" in schema["info"]["description"]
    assert "timeout_ms" in operation["description"]
    assert "requestBody" in operation
    logs = (tmp_path / "errors.jsonl").read_text()
    assert "private-password" not in logs
    events = [json.loads(line) for line in logs.splitlines()]
    assert [event["status"] for event in events] == [503, 404, 405]
    assert events[1]["route"] == "<unmatched>"


async def invoke(application, receive, send=None, headers=None):
    messages = []

    async def collect(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/jobs/search",
            "raw_path": b"/jobs/search",
            "query_string": b"",
            "root_path": "",
            "headers": headers or [(b"content-type", b"application/json")],
        },
        receive,
        send or collect,
    )
    return messages


@pytest.mark.parametrize("content_length", [None, b"1", b"99999999"])
def test_stream_byte_limit_ignores_content_length(client, content_length):
    async def run():
        chunks = iter([b" " * 8192, b" " * 8192, b"x"])

        async def receive():
            return {"type": "http.request", "body": next(chunks), "more_body": True}

        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", content_length))
        result = await invoke(client.app, receive, headers=headers)
        assert result[0]["status"] == 413

    anyio.run(run)


def test_total_body_deadline(client, monkeypatch):
    monkeypatch.setattr(app, "BODY_READ_SECONDS", 0.02)

    async def run():
        async def receive():
            await anyio.sleep(0.01)
            return {"type": "http.request", "body": b" ", "more_body": True}

        result = await invoke(client.app, receive)
        assert result[0]["status"] == 408

    anyio.run(run)


def test_exact_byte_limit_and_json_charset(client, monkeypatch):
    monkeypatch.setattr(database, "search_jobs", lambda _: [])
    body = json.dumps(LEAF).encode()
    response = client.post(
        "/jobs/search",
        content=body + b" " * (16384 - len(body)),
        headers={"Content-Type": "Application/JSON; charset=utf-8"},
    )
    assert response.status_code == 200


def test_disconnect_and_failed_delivery_are_aborts(client, tmp_path):
    async def disconnected():
        return {"type": "http.disconnect"}

    anyio.run(invoke, client.app, disconnected)

    async def receive():
        return {"type": "http.request", "body": b"{}"}

    async def broken_send(message):
        raise OSError("transport-private-secret")

    with pytest.raises(OSError):
        anyio.run(invoke, client.app, receive, broken_send)
    logs = (tmp_path / "errors.jsonl").read_text()
    events = [json.loads(line) for line in logs.splitlines()]
    assert len(events) == 2
    assert all(event["event"] == "request_aborted" and event["status"] is None for event in events)
    assert "transport-private-secret" not in logs


def test_database_dispatch_keeps_event_loop_and_health_responsive(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    worker_threads = []

    def search(_):
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        return []

    def ready():
        worker_threads.append(threading.get_ident())

    monkeypatch.setattr(database, "search_jobs", search)
    monkeypatch.setattr(database, "check_database", ready)

    async def run():
        loop_thread = threading.get_ident()

        async def receive():
            return {"type": "http.request", "body": json.dumps(LEAF).encode()}

        task = asyncio.create_task(invoke(client.app, receive))
        while not started.is_set():
            await anyio.sleep(0)
        try:
            # The test loop would stall here if search were dispatched inline.
            assert not task.done()
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
        finally:
            release.set()
        assert (await task)[0]["status"] == 200
        assert all(worker != loop_thread for worker in worker_threads)

    anyio.run(run)


def test_anyio_cancellation_waits_for_database_cleanup(client, monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()
    finished = threading.Event()

    def search(_):
        started.set()
        try:
            assert release.wait(2)
            return []
        finally:
            cleaned.set()

    monkeypatch.setattr(database, "search_jobs", search)

    async def run():
        scope = anyio.CancelScope()

        async def receive():
            return {"type": "http.request", "body": json.dumps(LEAF).encode()}

        async def request():
            try:
                with scope:
                    await invoke(client.app, receive)
            finally:
                finished.set()

        async with anyio.create_task_group() as group:
            group.start_soon(request)
            with anyio.fail_after(1):
                while not started.is_set():
                    await anyio.sleep(0.001)
            scope.cancel()
            try:
                await anyio.sleep(0.01)
                assert not finished.is_set()
                assert not cleaned.is_set()
            finally:
                release.set()
        assert cleaned.is_set()
        assert finished.is_set()

    anyio.run(run)


def test_json_string_brackets_and_maximum_valid_expression_depth(client, monkeypatch):
    monkeypatch.setattr(database, "search_jobs", lambda _: [])
    expression = {"field": "technology", "value": "[" * 100 + '\\"' + "]" * 100}
    for _ in range(15):
        expression = {"and": [expression]}
    assert client.post("/jobs/search", json={"query": expression}).status_code == 200
