"""Core HTTP search, health, and fail-closed error tests.

Coverage gaps:
- Invalid JSON variants (truncated, non-UTF8, NaN, 64-container nesting)
- Unsupported media type and empty Content-Type
- 504 statement_timeout and 500 internal_error mapping
- OpenAPI description/response contract
- Streamed body limit ignoring Content-Length
- 5s body-read deadline (408)
- Exact 16384-byte body plus charset=utf-8
- Client disconnect and failed response delivery abort logging
- Search dispatched off the event loop so /health stays responsive
- AnyIO cancellation waits for database worker cleanup
- Maximum valid expression depth over HTTP
"""

import os
import subprocess
import sys
from uuid import UUID

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


def test_search_returns_a_bare_typed_array(client, monkeypatch):
    def search(request):
        assert request.options.limit == 10
        return [Job(id=12, organization_name="Apple")]

    monkeypatch.setattr(database, "search_jobs", search)
    response = client.post("/jobs/search", json=LEAF, headers={"X-Request-ID": "client-secret"})
    assert response.status_code == 200
    assert response.json() == [Job(id=12, organization_name="Apple").model_dump(mode="json")]
    assert UUID(response.headers["x-request-id"]).version == 4
    assert response.headers["x-request-id"] != "client-secret"


def test_invalid_input_is_safe_and_never_searches(client, monkeypatch):
    def unexpected(_):
        raise AssertionError("Validation must precede SQL")

    monkeypatch.setattr(database, "search_jobs", unexpected)
    extra = client.post("/jobs/search", json={"secret-key": "private"})
    too_large = client.post(
        "/jobs/search",
        content=b"x" * 16385,
        headers={"Content-Type": "application/json"},
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "invalid_request"
    assert extra.json()["error"]["request_id"] == extra.headers["x-request-id"]
    assert "private" not in extra.text
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "body_too_large"


def test_database_errors_are_safe(client, monkeypatch, capsys):
    def fail(_):
        raise database.DatabaseUnavailableError("password-secret")

    monkeypatch.setattr(database, "search_jobs", fail)
    response = client.post("/jobs/search", json=LEAF)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    captured = capsys.readouterr()
    assert "password-secret" not in response.text + captured.out + captured.err


def test_health_stays_up_when_ready_fails(client, monkeypatch):
    monkeypatch.setattr(database, "check_database", lambda: None)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200

    def unavailable():
        raise database.DatabaseUnavailableError("password-secret")

    monkeypatch.setattr(database, "check_database", unavailable)
    assert client.get("/health").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert "password-secret" not in ready.text
