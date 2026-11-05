"""Database lifecycle unit tests and opt-in isolated PostgreSQL behavior tests."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import psycopg
import pytest
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
