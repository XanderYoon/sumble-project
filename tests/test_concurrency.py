"""Core search-admission tests.

Coverage gaps:
- Invalid MAX_CONCURRENT_SEARCHES values other than empty string (0, 17, negatives, floats, Unicode)
- Default capacity 4 and maximum capacity 16
- Readiness shares a one-slot semaphore with searches
- Overflow does not allocate/read the request body
- Validation, 400, 413, worker 503/504/500, upload timeout, disconnect, and transport
  failure each release a slot
- AnyIO vs asyncio cancellation during upload
- Cancellation retains the slot through worker cleanup for /jobs/search and /ready
- Repeated asyncio cancellation cannot detach cleanup
- Isolated Postgres statement-timeout recovery of HTTP capacity
- Isolated Postgres cancelled SQL stays admitted until the connection closes
"""

import asyncio
import json
import threading

import anyio
import pytest

from sumble_api import app, database

PAYLOAD = {"query": {"field": "technology", "value": "python"}}


@pytest.fixture
def application(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_CONCURRENT_SEARCHES", "1")
    return app.create_app()


async def invoke(application, path="/jobs/search", receive=None, send=None):
    messages = []

    async def default_receive():
        return {"type": "http.request", "body": json.dumps(PAYLOAD).encode()}

    async def collect(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST" if path == "/jobs/search" else "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
        },
        receive or default_receive,
        send or collect,
    )
    return messages


def status(messages):
    return messages[0]["status"]


def test_invalid_capacity_fails_application_construction(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_CONCURRENT_SEARCHES", "")
    with pytest.raises(ValueError, match="MAX_CONCURRENT_SEARCHES"):
        app.create_app()


def test_one_search_slot_rejects_overflow_and_recovers(application, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def search(_):
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(database, "search_jobs", search)

    async def run():
        held = asyncio.create_task(invoke(application))
        with anyio.fail_after(3):
            while not started.is_set():
                await anyio.sleep(0.001)
        busy = await invoke(application)
        assert status(busy) == 503
        headers = dict(busy[0]["headers"])
        assert headers[b"retry-after"] == b"1"
        assert json.loads(busy[1]["body"])["error"]["code"] == "server_busy"
        assert status(await invoke(application, "/health")) == 200
        release.set()
        assert status(await held) == 200
        monkeypatch.setattr(database, "search_jobs", lambda _: [])
        assert status(await invoke(application)) == 200

    anyio.run(run)
