"""Contract for compiling validated expressions into parameterized SQL sets."""

from dataclasses import dataclass

from sumble_api.query import Expression


@dataclass(frozen=True)
class CompiledQuery:
    """Hold a job-ID SQL set and separate ordered parameter values."""

    sql: str
    parameters: tuple[object, ...]


def compile_query(expression: Expression) -> CompiledQuery:
    """Compile a validated tree using fixed SQL fragments and bound values.

    Args:
        expression: Validated bounded Boolean expression.

    Returns:
        A SQL set of job IDs and parameters, without database execution.

    Raises:
        NotImplementedError: SQL compilation is not implemented in the skeleton.
    """
    raise NotImplementedError("SQL compilation is scheduled for implementation.")
