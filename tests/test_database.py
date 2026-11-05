"""Database lifecycle unit tests and opt-in isolated PostgreSQL behavior tests."""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError

from sumble_api import database, query


def leaf(field="technology", value="python"):
    return {"field": field, "value": value}


def request(expression=None, **options):
    return query.parse_query({"query": leaf() if expression is None else expression, **options})


@pytest.fixture
def fake_connection(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"id": 7}]
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(database.psycopg, "connect", connect)
    return connect, connection, cursor


def test_parameterized_page_and_connection_controls(fake_connection):
    connect, connection, cursor = fake_connection
    result = database.search_jobs(
        request(leaf(value="x'); DROP TABLE job_posts; --"), offset=17, limit=2, timeout_ms=123)
    )
    assert [job.id for job in result] == [7]
    assert connect.call_args.kwargs["connect_timeout"] == 3
    assert connection.read_only is True
    assert cursor.execute.call_args_list[0].args == (
        "SELECT set_config('statement_timeout', %s, true)",
        ("123ms",),
    )
    statement, parameters = cursor.execute.call_args.args
    rendered = statement.as_string()
    assert "DROP TABLE" not in rendered
    assert "ORDER BY job.id ASC NULLS LAST LIMIT %s OFFSET %s" in rendered
    assert parameters == ("x'); DROP TABLE job_posts; --", 2, 17)
    connection.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (psycopg.OperationalError("secret diagnostics"), "DatabaseUnavailableError"),
        (psycopg.errors.QueryCanceled("secret diagnostics"), "SearchTimeoutError"),
    ],
)
def test_sql_failures_are_safe_and_close_transaction(fake_connection, failure, expected):
    _, connection, cursor = fake_connection
    cursor.execute.side_effect = [None, failure]
    with pytest.raises(getattr(database, expected)) as error:
        database.search_jobs(request())
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert connection.__exit__.call_args.args[0] is type(failure)


def test_conversion_failure_exits_transaction(fake_connection):
    _, connection, cursor = fake_connection
    cursor.fetchall.return_value = [{"id": None}]
    with pytest.raises(ValidationError):
        database.search_jobs(request())
    assert connection.__exit__.call_args.args[0] is ValidationError


def test_connection_failure_is_safe(monkeypatch):
    def fail(**kwargs):
        raise psycopg.OperationalError("password=secret")

    monkeypatch.setattr(database.psycopg, "connect", fail)
    for operation in (lambda: database.search_jobs(request()), database.check_database):
        with pytest.raises(database.DatabaseUnavailableError) as error:
            operation()
        assert "secret" not in str(error.value)


def test_readiness_uses_read_only_bounded_schema_query(fake_connection):
    _, connection, cursor = fake_connection
    database.check_database()
    assert connection.read_only is True
    assert cursor.execute.call_args_list[0].args[1] == ("2000ms",)
    statement = cursor.execute.call_args.args[0]
    for table in (
        "job_posts",
        "job_posts_details",
        "organizations",
        "job_posts_tech",
        "tech",
        "job_posts_job_functions",
        "job_functions",
    ):
        assert table in statement
    assert "WHERE false" in statement


@pytest.fixture
def postgres_schema(monkeypatch):
    if os.environ.get("SUMBLE_RUN_INTEGRATION") != "1":
        pytest.skip("Set SUMBLE_RUN_INTEGRATION=1 and PG variables for real PostgreSQL tests")
    schema = "sumble_test_" + uuid4().hex
    real_connect = psycopg.connect
    connections = []
    backend_pids = []
    with real_connect(connect_timeout=3, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            admin.execute("""
                CREATE TABLE job_posts (
                    id bigint, organization_id bigint, country_id bigint,
                    datetime_pulled timestamptz
                );
                CREATE TABLE detail_rows (id bigint, job_title text, location text);
                CREATE VIEW job_posts_details AS SELECT * FROM detail_rows;
                CREATE TABLE organizations (id bigint, name text);
                CREATE TABLE tech (id bigint, name text);
                CREATE TABLE job_functions (id bigint, name text);
                CREATE TABLE job_posts_tech (job_post_id bigint, tech_id bigint);
                CREATE TABLE job_posts_job_functions (job_post_id bigint, job_function_id bigint);
                INSERT INTO organizations VALUES (1, 'Apple'), (2, 'Apple Services'), (3, NULL);
                INSERT INTO job_posts VALUES
                    (1, 1, 10, '2024-01-01Z'), (2, 1, NULL, '2024-01-01Z'),
                    (3, 2, 10, '2024-01-02Z'), (4, NULL, NULL, NULL),
                    (5, 3, NULL, NULL), (6, 999, 20, '2024-01-02Z'),
                    (7, 1, 20, '2024-01-03Z'), (8, 2, 20, '2024-01-03Z');
                INSERT INTO detail_rows VALUES
                    (1, 'Alpha', 'London'), (2, 'Alpha', NULL), (3, 'Zulu', 'Paris'),
                    (5, NULL, NULL), (6, 'Beta', 'Rome'), (7, 'Beta', NULL), (8, 'Zulu', NULL);
                INSERT INTO tech VALUES
                    (1, 'Python'), (2, 'sql'), (3, '.net'), (4, 'psql'), (5, NULL),
                    (6, 'x''); DROP TABLE job_posts; --'), (7, 'pythonista'),
                    (8, '%'), (9, 'a_b');
                INSERT INTO job_functions VALUES (1, 'Engineering'), (2, 'Sales'), (3, NULL);
                INSERT INTO job_posts_tech VALUES
                    (1, 1), (1, 1), (1, 2), (1, 3), (2, 1), (2, 4),
                    (3, 2), (4, NULL), (5, 5), (6, 6), (7, 7), (8, 8), (8, 9), (999, 1);
                INSERT INTO job_posts_job_functions VALUES
                    (1, 1), (1, 1), (2, 2), (3, 1), (4, NULL), (5, 3), (6, 2);
            """)

            def isolated_connect(*args, **kwargs):
                kwargs["options"] = f"-c search_path={schema}"
                connection = real_connect(*args, **kwargs)
                connections.append(connection)
                backend_pids.append(connection.info.backend_pid)
                return connection

            monkeypatch.setattr(database.psycopg, "connect", isolated_connect)
            yield admin, connections, backend_pids
        finally:
            for connection in connections:
                connection.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def ids(expression=None, **options):
    return [job.id for job in database.search_jobs(request(expression, **options))]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (leaf(value="PYTHON"), [1, 2]),
        (leaf(value="pythonista"), [7]),
        (leaf(value=".NET"), [1]),
        (leaf(value="psql"), [2]),
        (leaf(value="%"), [8]),
        (leaf(value="a_b"), [8]),
        (leaf(value="x'); DROP TABLE job_posts; --"), [6]),
        (leaf(value="unknown"), []),
        (leaf(value=" python "), []),
        (leaf("organization", "apple"), [1, 2, 7]),
        (leaf("job_function", "engineering"), [1, 3]),
        ({"and": [leaf(), leaf(value="SQL")]}, [1]),
        ({"or": [leaf(), leaf(), leaf("job_function", "Engineering")]}, [1, 2, 3]),
        ({"not": leaf()}, [3, 4, 5, 6, 7, 8]),
        ({"not": leaf("organization", "Apple")}, [3, 4, 5, 6, 8]),
        ({"not": leaf("job_function", "Engineering")}, [2, 4, 5, 6, 7, 8]),
        ({"not": {"not": leaf()}}, [1, 2]),
        (
            {
                "and": [
                    {"or": [leaf(), leaf("job_function", "Engineering")]},
                    {"not": {"and": [leaf(value="sql"), leaf("organization", "Apple")]}},
                ]
            },
            [2, 3],
        ),
    ],
)
def test_real_boolean_semantics(postgres_schema, expression, expected):
    assert ids(expression) == expected
    assert postgres_schema[1][-1].closed


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "direction", "expected"),
    [
        ("id", "asc", [1, 2, 3, 4, 5, 6, 7, 8]),
        ("id", "desc", [8, 7, 6, 5, 4, 3, 2, 1]),
        ("datetime_pulled", "asc", [1, 2, 3, 6, 7, 8, 4, 5]),
        ("datetime_pulled", "desc", [7, 8, 3, 6, 1, 2, 4, 5]),
        ("job_title", "asc", [1, 2, 6, 7, 3, 8, 4, 5]),
        ("job_title", "desc", [3, 8, 6, 7, 1, 2, 4, 5]),
        ("organization_name", "asc", [1, 2, 7, 3, 8, 4, 5, 6]),
        ("organization_name", "desc", [3, 8, 1, 2, 7, 4, 5, 6]),
    ],
)
def test_real_sorting_before_paging(postgres_schema, field, direction, expected):
    expression = {"not": leaf(value="absent")}
    options = {"sort": {"field": field, "direction": direction}}
    assert ids(expression, **options) == expected
    pages = [ids(expression, offset=offset, limit=3, **options) for offset in (0, 3, 6)]
    assert [identifier for page in pages for identifier in page] == expected
    assert ids(expression, offset=8, **options) == []
    assert ids(expression, offset=100_000, **options) == []


@pytest.mark.integration
def test_real_nullable_hydration_and_readiness(postgres_schema):
    admin, connections, _ = postgres_schema
    database.check_database()
    jobs = database.search_jobs(request({"not": leaf(value="absent")}))
    assert jobs[0].model_dump() == {
        "id": 1,
        "organization_id": 1,
        "organization_name": "Apple",
        "job_title": "Alpha",
        "location": "London",
        "country_id": 10,
        "datetime_pulled": datetime(2024, 1, 1, tzinfo=UTC),
    }
    assert jobs[3].model_dump() == {
        "id": 4,
        "organization_id": None,
        "organization_name": None,
        "job_title": None,
        "location": None,
        "country_id": None,
        "datetime_pulled": None,
    }
    assert jobs[5].organization_id == 999 and jobs[5].organization_name is None
    assert all(connection.closed for connection in connections)
    admin.execute("ALTER TABLE tech RENAME COLUMN name TO missing_name")
    with pytest.raises(database.DatabaseUnavailableError):
        database.check_database()
    assert connections[-1].closed


@pytest.mark.integration
def test_real_timeout_cancellation_cleanup_and_recovery(postgres_schema):
    admin, connections, _ = postgres_schema
    # Every hydrated row must execute a one-second sleep, reliably exceeding 50 ms.
    admin.execute("""
        CREATE FUNCTION slow_location(value text) RETURNS text LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_sleep(1);
            RETURN value;
        END $$;
        CREATE OR REPLACE VIEW job_posts_details AS
            SELECT id, job_title, slow_location(location) AS location FROM detail_rows;
    """)
    with pytest.raises(database.SearchTimeoutError, match=r"^Search statement timed out[.]$"):
        database.search_jobs(request(timeout_ms=50))
    cancelled = connections[-1]
    assert cancelled.closed
    assert admin.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (postgres_schema[2][-1],)
    ).fetchone() == (0,)
    admin.execute("CREATE OR REPLACE VIEW job_posts_details AS SELECT * FROM detail_rows")
    assert ids() == [1, 2]
    assert connections[-1].closed


@pytest.mark.integration
def test_real_sql_and_conversion_failures_close_connections(postgres_schema):
    admin, connections, _ = postgres_schema
    admin.execute("ALTER TABLE tech RENAME COLUMN name TO missing_name")
    with pytest.raises(database.DatabaseUnavailableError):
        database.search_jobs(request())
    assert connections[-1].closed
    admin.execute("ALTER TABLE tech RENAME COLUMN missing_name TO name")
    admin.execute("ALTER TABLE job_posts ALTER COLUMN country_id TYPE text")
    admin.execute("UPDATE job_posts SET country_id = 'invalid' WHERE id = 1")
    with pytest.raises(ValidationError):
        database.search_jobs(request())
    assert connections[-1].closed
    admin.execute("UPDATE job_posts SET country_id = NULL WHERE id = 1")
    assert ids() == [1, 2]


@pytest.mark.integration
def test_real_transaction_is_read_only_and_timeout_is_local(postgres_schema):
    admin, _, _ = postgres_schema
    # A function invoked during hydration observes transaction settings directly.
    admin.execute("""
        CREATE FUNCTION transaction_settings(value text) RETURNS text LANGUAGE sql AS $$
            SELECT current_setting('transaction_read_only') || ':' ||
                   current_setting('statement_timeout')
        $$;
        CREATE OR REPLACE VIEW job_posts_details AS
            SELECT id, job_title, transaction_settings(location) AS location FROM detail_rows;
    """)
    assert database.search_jobs(request(timeout_ms=321))[0].location == "on:321ms"
    assert database.search_jobs(request())[0].location == "on:25s"
