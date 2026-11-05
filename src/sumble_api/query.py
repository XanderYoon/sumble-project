"""Typed search contracts; runtime validation is implemented in a later ticket."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type FilterField = Literal["technology", "job_function", "organization"]
type SortField = Literal["id", "datetime_pulled", "job_title", "organization_name"]
type SortDirection = Literal["asc", "desc"]

DEFAULT_LIMIT = 10
MAX_LIMIT = 10
MAX_OFFSET = 100_000
DEFAULT_TIMEOUT_MS = 25_000
MAX_TIMEOUT_MS = 25_000
MAX_BODY_BYTES = 16_384
MAX_QUERY_NODES = 128
MAX_QUERY_DEPTH = 16
MAX_VALUE_LENGTH = 256


class QueryValidationError(ValueError):
    """Signal invalid input with a safe message and validation path."""


@dataclass(frozen=True)
class Leaf:
    """Match a single allowlisted field and literal name."""

    field: FilterField
    value: str


@dataclass(frozen=True)
class Conjunction:
    """Require every child expression to match a job."""

    children: tuple[Expression, ...]


@dataclass(frozen=True)
class Disjunction:
    """Require at least one child expression to match a job."""

    children: tuple[Expression, ...]


@dataclass(frozen=True)
class Negation:
    """Match jobs absent from the child expression's result set."""

    child: Expression


type Expression = Leaf | Conjunction | Disjunction | Negation


@dataclass(frozen=True)
class SearchOptions:
    """Hold validated paging, ordering, and SQL-timeout options."""

    limit: int = DEFAULT_LIMIT
    offset: int = 0
    sort_field: SortField = "id"
    sort_direction: SortDirection = "asc"
    timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclass(frozen=True)
class SearchRequest:
    """Hold a validated Boolean tree and immutable search options."""

    query: Expression
    options: SearchOptions = SearchOptions()


def parse_query(payload: object) -> SearchRequest:
    """Validate decoded request data and apply documented control defaults.

    Args:
        payload: Entire decoded JSON envelope, including optional controls.

    Returns:
        A bounded expression tree and validated search options.

    Raises:
        QueryValidationError: A shape, type, name, or resource bound is invalid.
        NotImplementedError: Validation is not implemented in the skeleton.
    """
    raise NotImplementedError("Query validation is scheduled for implementation.")
