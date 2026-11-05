"""Database boundary; importing this module never connects to PostgreSQL."""

from sumble_api.models import Job
from sumble_api.query import SearchRequest


class DatabaseUnavailableError(RuntimeError):
    """Signal a connection or readiness failure without leaking driver details."""


class SearchTimeoutError(TimeoutError):
    """Signal that PostgreSQL cancelled the search statement on timeout."""


def search_jobs(request: SearchRequest) -> list[Job]:
    """Execute a read-only search using PG environment configuration.

    Args:
        request: Validated tree plus paging, sorting, and timeout options.

    Returns:
        Up to ten unique jobs in the requested order and offset.

    Raises:
        DatabaseUnavailableError: PostgreSQL cannot be reached.
        SearchTimeoutError: The bounded statement timeout is exceeded.
        NotImplementedError: Execution is not implemented in the skeleton.
    """
    raise NotImplementedError("Database search is scheduled for implementation.")


def check_database() -> None:
    """Check connectivity and required schema using a bounded readiness query.

    Raises:
        DatabaseUnavailableError: The database or required schema is unavailable.
        NotImplementedError: Readiness is not implemented in the skeleton.
    """
    raise NotImplementedError("Database readiness is scheduled for implementation.")
