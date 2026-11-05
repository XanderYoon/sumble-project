"""Bounded read-only PostgreSQL execution; imports never open connections."""

from typing import LiteralString, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from sumble_api import compiler, models, query

_SORT_FIELDS: dict[query.SortField, LiteralString] = {
    "id": "job.id",
    "datetime_pulled": "job.datetime_pulled",
    "job_title": "details.job_title",
    "organization_name": "organization.name",
}
_SORT_DIRECTIONS: dict[query.SortDirection, LiteralString] = {"asc": "ASC", "desc": "DESC"}
_PROJECTION = """
    SELECT job.id, job.organization_id, organization.name AS organization_name,
           details.job_title, details.location, job.country_id, job.datetime_pulled
    FROM job_posts AS job
    LEFT JOIN job_posts_details AS details ON details.id = job.id
    LEFT JOIN organizations AS organization ON organization.id = job.organization_id
"""
_READINESS_SQL = (
    _PROJECTION
    + """
    LEFT JOIN job_posts_tech AS technology_link ON technology_link.job_post_id = job.id
    LEFT JOIN tech AS technology ON technology.id = technology_link.tech_id
    LEFT JOIN job_posts_job_functions AS function_link ON function_link.job_post_id = job.id
    LEFT JOIN job_functions AS function ON function.id = function_link.job_function_id
    WHERE false AND technology.name IS NULL AND function.name IS NULL
"""
)


class DatabaseUnavailableError(RuntimeError):
    """Signal a database failure without exposing driver diagnostics."""


class SearchTimeoutError(TimeoutError):
    """Signal that PostgreSQL cancelled a search on statement timeout."""


def search_jobs(request: query.SearchRequest) -> list[models.Job]:
    """Filter actual jobs, order all matches, and hydrate one bounded page.

    Args:
        request: Validated tree and immutable paging, sorting, timeout options.

    Returns:
        Unique jobs with nullable source fields in deterministic page order.

    Raises:
        DatabaseUnavailableError: Connection or SQL execution failed.
        SearchTimeoutError: PostgreSQL cancelled the bounded statement.
        pydantic.ValidationError: Source values cannot form a public job.
    """
    compiled = compiler.compile_query(request.query)
    options = request.options
    ordering = sql.SQL("{} {} NULLS LAST").format(
        sql.SQL(_SORT_FIELDS[options.sort_field]),
        sql.SQL(_SORT_DIRECTIONS[options.sort_direction]),
    )
    if options.sort_field != "id":
        ordering += sql.SQL(", job.id ASC")
    # The compiler emits only fixed SQL fragments; all leaf values stay in parameters.
    matches = sql.SQL(cast(LiteralString, compiled.sql))
    statement = sql.SQL(
        _PROJECTION + " WHERE job.id IN ({matches}) ORDER BY {ordering} LIMIT %s OFFSET %s"
    ).format(matches=matches, ordering=ordering)
    try:
        with psycopg.connect(connect_timeout=3) as connection:
            connection.read_only = True
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{options.timeout_ms}ms",),
                )
                cursor.execute(statement, (*compiled.parameters, options.limit, options.offset))
                return [models.Job.model_validate(row) for row in cursor.fetchall()]
    except psycopg.errors.QueryCanceled:
        raise SearchTimeoutError("Search statement timed out.") from None
    except psycopg.Error:
        raise DatabaseUnavailableError("Database search is unavailable.") from None


def check_database() -> None:
    """Check required relations and columns in a bounded read-only transaction.

    Raises:
        DatabaseUnavailableError: Connectivity, schema, or readiness timeout failed.
    """
    try:
        with psycopg.connect(connect_timeout=3) as connection:
            connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", ("2000ms",))
                cursor.execute(_READINESS_SQL)
    except psycopg.Error:
        raise DatabaseUnavailableError("Database is not ready.") from None
