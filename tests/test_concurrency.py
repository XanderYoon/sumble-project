"""Deterministic admission bounds and cleanup recovery through the HTTP boundary."""

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


async def invoke(application, path="/jobs/search", payload=None, receive=None, send=None):
    messages = []

    async def default_receive():
        return {
            "type": "http.request",
            "body": json.dumps(PAYLOAD if payload is None else payload).encode(),
        }

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


def assert_busy(messages):
    assert status(messages) == 503
    headers = dict(messages[0]["headers"])
    assert headers[b"retry-after"] == b"1"
    error = json.loads(messages[1]["body"])["error"]
    assert error["code"] == "server_busy"
    assert error["request_id"] == headers[b"x-request-id"].decode()
    return error["request_id"]


@pytest.mark.parametrize("value", ["", "0", "17", "-1", "1.5", "true", " 4", "\uff14"])
def test_invalid_capacity_fails_application_construction(monkeypatch, tmp_path, value):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_CONCURRENT_SEARCHES", value)
    with pytest.raises(ValueError, match="MAX_CONCURRENT_SEARCHES"):
        app.create_app()


@pytest.mark.parametrize("capacity", [None, 1, 16])
def test_search_and_readiness_bounds_before_body_read(monkeypatch, tmp_path, capacity):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    if capacity is None:
        monkeypatch.delenv("MAX_CONCURRENT_SEARCHES", raising=False)
    else:
        monkeypatch.setenv("MAX_CONCURRENT_SEARCHES", str(capacity))
    application = app.create_app()
    expected = 4 if capacity is None else capacity

    async def run():
        loop = asyncio.get_running_loop()
        entered = asyncio.Queue()
        release = threading.Event()
        active = 0
        peak = 0
        lock = threading.Lock()

        def work(kind):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            loop.call_soon_threadsafe(entered.put_nowait, kind)
            try:
                assert release.wait(5)
            finally:
                with lock:
                    active -= 1
            return []

        monkeypatch.setattr(database, "search_jobs", lambda _: work("search"))
        monkeypatch.setattr(database, "check_database", lambda: work("ready"))
        tasks = []
        try:
            for _ in range(expected):
                tasks.append(asyncio.create_task(invoke(application)))
            with anyio.fail_after(3):
                assert [await entered.get() for _ in range(expected)] == ["search"] * expected
            tasks.append(asyncio.create_task(invoke(application, "/ready")))
            with anyio.fail_after(3):
                assert await entered.get() == "ready"

            async def forbidden_receive():
                pytest.fail("Overload must not read or allocate the request body")

            with anyio.fail_after(1):
                rejected = assert_busy(await invoke(application, receive=forbidden_receive))
                assert_busy(await invoke(application, "/ready", receive=forbidden_receive))
                assert status(await invoke(application, "/health")) == 200
            assert active == peak == expected + 1
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        assert all(status(result) == 200 for result in results)
        assert active == 0
        assert status(await invoke(application)) == 200
        assert status(await invoke(application, "/ready")) == 200
        events = [json.loads(line) for line in (tmp_path / "errors.jsonl").read_text().splitlines()]
        assert len(events) == 2
        assert {event["code"] for event in events} == {"server_busy"}
        assert events[0]["request_id"] == rejected
        assert all(event["status"] == 503 for event in events)

    anyio.run(run)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, 422),
        (database.DatabaseUnavailableError("private"), 503),
        (database.SearchTimeoutError("private"), 504),
        (ValueError("private"), 500),
    ],
)
def test_validation_and_worker_failure_release_capacity(
    application, monkeypatch, failure, expected
):
    def fail(_):
        raise failure

    async def run():
        if failure is None:
            response = await invoke(application, payload={})
        else:
            monkeypatch.setattr(database, "search_jobs", fail)
            response = await invoke(application)
        assert status(response) == expected
        monkeypatch.setattr(database, "search_jobs", lambda _: [])
        assert status(await invoke(application)) == 200

    anyio.run(run)


@pytest.mark.parametrize(("body", "expected"), [(b"{", 400), (b"x" * 16385, 413)])
def test_body_rejection_releases_capacity(application, monkeypatch, body, expected):
    monkeypatch.setattr(database, "search_jobs", lambda _: [])

    async def run():
        async def receive():
            return {"type": "http.request", "body": body}

        assert status(await invoke(application, receive=receive)) == expected
        assert status(await invoke(application)) == 200

    anyio.run(run)


@pytest.mark.parametrize("cancellation", ["anyio", "asyncio"])
def test_cancelled_upload_releases_capacity(application, monkeypatch, cancellation):
    monkeypatch.setattr(database, "search_jobs", lambda _: [])

    async def run():
        started = anyio.Event()
        scope = anyio.CancelScope()

        async def receive():
            started.set()
            await anyio.sleep_forever()

        async def request():
            with scope:
                await invoke(application, receive=receive)

        task = asyncio.create_task(request())
        await started.wait()
        assert_busy(await invoke(application))
        if cancellation == "anyio":
            scope.cancel()
            await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert status(await invoke(application)) == 200

    anyio.run(run)


def test_response_transport_failure_recovers_capacity(application, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "search_jobs", lambda _: [])

    async def run():
        async def disconnected_send(message):
            raise OSError("Disconnected transport")

        assert await invoke(application, send=disconnected_send) == []
        assert status(await invoke(application)) == 200
        event = json.loads((tmp_path / "errors.jsonl").read_text())
        assert event["event"] == "request_aborted"
        assert event["status"] is None

    anyio.run(run)


def test_upload_timeout_and_disconnect_release_capacity(application, monkeypatch):
    monkeypatch.setattr(app, "BODY_READ_SECONDS", 0.02)
    monkeypatch.setattr(database, "search_jobs", lambda _: [])

    async def run():
        started = anyio.Event()

        async def stalled_receive():
            started.set()
            await anyio.sleep_forever()

        task = asyncio.create_task(invoke(application, receive=stalled_receive))
        await started.wait()
        assert_busy(await invoke(application))
        assert status(await invoke(application, "/health")) == 200
        assert status(await task) == 408
        assert status(await invoke(application)) == 200

        async def disconnect():
            return {"type": "http.disconnect"}

        assert await invoke(application, receive=disconnect) == []
        assert status(await invoke(application)) == 200

    anyio.run(run)


@pytest.mark.parametrize("path", ["/jobs/search", "/ready"])
@pytest.mark.parametrize("cancellation", ["anyio", "asyncio"])
@pytest.mark.parametrize("worker_failure", [False, True])
def test_cancellation_retains_slot_through_worker_cleanup(
    application, monkeypatch, path, cancellation, worker_failure
):
    async def run():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release_work = threading.Event()
        release_cleanup = threading.Event()
        scope = anyio.CancelScope()

        def work(*_):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release_work.wait(5)
                if worker_failure:
                    raise database.DatabaseUnavailableError("private")
                return []
            finally:
                loop.call_soon_threadsafe(cleaning.set)
                assert release_cleanup.wait(5)

        monkeypatch.setattr(database, "search_jobs", work)
        monkeypatch.setattr(database, "check_database", work)

        async def request():
            with scope:
                return await invoke(application, path)

        task = asyncio.create_task(request())
        with anyio.fail_after(3):
            await entered.wait()
        try:
            if cancellation == "anyio":
                scope.cancel()
            else:
                task.cancel()
            assert_busy(await invoke(application, path))
            assert status(await invoke(application, "/health")) == 200
            release_work.set()
            with anyio.fail_after(3):
                await cleaning.wait()
            assert not task.done()
            assert_busy(await invoke(application, path))
            if cancellation == "asyncio":
                task.cancel()  # Repeated cancellation cannot detach the cleanup.
                assert_busy(await invoke(application, path))
        finally:
            release_work.set()
            release_cleanup.set()
        if cancellation == "asyncio":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        monkeypatch.setattr(database, "search_jobs", lambda _: [])
        monkeypatch.setattr(database, "check_database", lambda: None)
        assert status(await invoke(application, path)) == 200

    anyio.run(run)


def test_readiness_failure_recovers_independently(application, monkeypatch):
    def unavailable():
        raise database.DatabaseUnavailableError("private")

    async def run():
        monkeypatch.setattr(database, "check_database", unavailable)
        assert status(await invoke(application, "/ready")) == 503
        monkeypatch.setattr(database, "check_database", lambda: None)
        assert status(await invoke(application, "/ready")) == 200

    anyio.run(run)


@pytest.mark.integration
def test_real_statement_timeout_recovers_http_capacity(application, postgres_schema):
    admin, connections, pids = postgres_schema
    admin.execute("""
        CREATE FUNCTION slow_location(value text) RETURNS text LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_sleep(1);
            RETURN value;
        END $$;
        CREATE OR REPLACE VIEW job_posts_details AS
            SELECT id, job_title, slow_location(location) AS location FROM detail_rows;
    """)

    async def run():
        response = await invoke(application, payload={**PAYLOAD, "timeout_ms": 50})
        assert status(response) == 504
        assert json.loads(response[1]["body"])["error"]["code"] == "statement_timeout"
        assert connections[-1].closed
        assert admin.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pids[-1],)
        ).fetchone() == (0,)
        admin.execute("CREATE OR REPLACE VIEW job_posts_details AS SELECT * FROM detail_rows")
        recovered = await invoke(application)
        assert status(recovered) == 200
        assert [job["id"] for job in json.loads(recovered[1]["body"])] == [1, 2]
        assert all(connection.closed for connection in connections)

    anyio.run(run)


@pytest.mark.integration
@pytest.mark.parametrize("cancellation", ["anyio", "asyncio"])
def test_real_cancelled_sql_stays_admitted_until_closed(application, postgres_schema, cancellation):
    admin, connections, pids = postgres_schema
    admin.execute("SELECT pg_advisory_lock(246810)")
    admin.execute("""
        CREATE FUNCTION blocked_location(value text) RETURNS text LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(246810);
            RETURN value;
        END $$;
        CREATE OR REPLACE VIEW job_posts_details AS
            SELECT id, job_title, blocked_location(location) AS location FROM detail_rows;
    """)

    async def run():
        scope = anyio.CancelScope()

        async def request():
            with scope:
                await invoke(application)

        task = asyncio.create_task(request())
        try:
            with anyio.fail_after(3):
                while True:
                    if pids and admin.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid = %s", (pids[0],)
                    ).fetchone() == ("advisory",):
                        break
                    await anyio.sleep(0.001)
            if cancellation == "anyio":
                scope.cancel()
            else:
                task.cancel()
            assert_busy(await invoke(application))
            assert not connections[0].closed
            assert status(await invoke(application, "/health")) == 200
            assert status(await invoke(application, "/ready")) == 200
            assert not task.done()
        finally:
            admin.execute("SELECT pg_advisory_unlock(246810)")
            if cancellation == "asyncio":
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
        assert connections[0].closed
        assert admin.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pids[0],)
        ).fetchone() == (0,)
        assert status(await invoke(application)) == 200
        assert all(connection.closed for connection in connections)

    anyio.run(run)
