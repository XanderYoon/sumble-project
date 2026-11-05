"""Compile validated Boolean expressions into parameterized job-ID SQL sets."""

from dataclasses import dataclass

from sumble_api import query

_LEAF_SQL: dict[query.FilterField, str] = {
    "technology": (
        "SELECT DISTINCT association.job_post_id AS id "
        "FROM job_posts_tech AS association "
        "JOIN tech AS dictionary ON dictionary.id = association.tech_id "
        "WHERE lower(dictionary.name) = lower(%s)"
    ),
    "job_function": (
        "SELECT DISTINCT association.job_post_id AS id "
        "FROM job_posts_job_functions AS association "
        "JOIN job_functions AS dictionary ON dictionary.id = association.job_function_id "
        "WHERE lower(dictionary.name) = lower(%s)"
    ),
    "organization": (
        "SELECT DISTINCT job.id AS id FROM job_posts AS job "
        "JOIN organizations AS dictionary ON dictionary.id = job.organization_id "
        "WHERE lower(dictionary.name) = lower(%s)"
    ),
}


@dataclass(frozen=True)
class CompiledQuery:
    """Hold a job-ID SQL set and separate ordered parameter values."""

    sql: str
    parameters: tuple[object, ...]


def compile_query(expression: query.Expression) -> CompiledQuery:
    """Compile a validated tree using fixed SQL fragments and bound values.

    Args:
        expression: Validated bounded Boolean expression.

    Returns:
        A parenthesized SQL set of job IDs and depth-first, left-to-right
        parameters, without database execution. Literal names are passed
        unchanged so PostgreSQL applies its own case rules.
    """
    parameters: list[object] = []
    sql = _compile_expression(expression, parameters)
    return CompiledQuery(sql, tuple(parameters))


def _compile_expression(expression: query.Expression, parameters: list[object]) -> str:
    """Group every subtree to preserve nesting and SQL set precedence."""
    if isinstance(expression, query.Leaf):
        parameters.append(expression.value)
        return f"({_LEAF_SQL[expression.field]})"
    if isinstance(expression, query.Negation):
        child = _compile_expression(expression.child, parameters)
        return f"(SELECT id FROM job_posts EXCEPT {child})"

    operator = " INTERSECT " if isinstance(expression, query.Conjunction) else " UNION "
    children = [_compile_expression(child, parameters) for child in expression.children]
    return f"({operator.join(children)})"
