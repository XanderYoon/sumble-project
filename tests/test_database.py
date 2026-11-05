"""Core search execution and isolated Boolean-matching tests.

Coverage gaps:
- Row ValidationError closes the transaction
- Readiness probe enumerates every required table with WHERE false
- Full isolated Boolean matrix (pythonista, .NET, psql, %, a_b, injection name, unknown,
  padded values, job_function, nested AND/OR/NOT, double NOT)
- Full sort-field/direction matrix and limit/offset page concatenation
- Nullable job hydration and missing organization name
- Schema-rename readiness failure
- SQL plus conversion failures close connections and recover
- Transaction read-only and statement_timeout observed via a SQL function
"""

from unittest.mock import MagicMock

import psycopg
import pytest

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
    assert connection.__exit__.call_args.args[0] is type(failure)


def test_connection_failure_is_safe(monkeypatch):
    def fail(**kwargs):
        raise psycopg.OperationalError("password=secret")

    monkeypatch.setattr(database.psycopg, "connect", fail)
    with pytest.raises(database.DatabaseUnavailableError) as error:
        database.search_jobs(request())
    assert "secret" not in str(error.value)


def ids(expression=None, **options):
    return [job.id for job in database.search_jobs(request(expression, **options))]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (leaf(value="PYTHON"), [1, 2]),
        (leaf("organization", "apple"), [1, 2, 7]),
        ({"and": [leaf(), leaf(value="SQL")]}, [1]),
        ({"not": leaf()}, [3, 4, 5, 6, 7, 8]),
    ],
)
def test_real_boolean_semantics(postgres_schema, expression, expected):
    assert ids(expression) == expected
    assert postgres_schema[1][-1].closed


@pytest.mark.integration
def test_real_timeout_cancellation_cleanup_and_recovery(postgres_schema):
    admin, connections, _ = postgres_schema
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
    assert connections[-1].closed
    admin.execute("CREATE OR REPLACE VIEW job_posts_details AS SELECT * FROM detail_rows")
    assert ids() == [1, 2]
