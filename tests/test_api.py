"""HTTP test scaffolding; route acceptance tests arrive with implementation."""

import importlib

import psycopg


def test_importing_modules_does_not_connect_to_database(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Importing application modules must not open a database.")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    for name in ("app", "query", "compiler", "database", "models", "observability"):
        importlib.reload(importlib.import_module(f"sumble_api.{name}"))
