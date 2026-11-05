"""Pure, bounded validation of Boolean searches and their controls.

Paging controls (``limit``, ``offset``, ``sort``, ``timeout_ms``) only bound a
run. The ``query`` member is the search, and it is a tree: nesting changes
which jobs match. This module walks that JSON into typed ``Leaf`` /
``Conjunction`` / ``Disjunction`` / ``Negation`` nodes. The compiler later maps
only that known tree onto parameterized SQL:

    Leaf  -> job-id subquery with a bound value
    AND   -> INTERSECT
    OR    -> UNION
    NOT   -> EXCEPT

Accepted — Apple AND (.net OR statistician), depth 3, 5 nodes:

        AND
        ├── organization = Apple
        └── OR
            ├── technology = .net
            └── job_function = statistician

Same leaves, different tree, different jobs — (Apple AND .net) OR statistician:

        OR
        ├── AND
        │   ├── organization = Apple
        │   └── technology = .net
        └── job_function = statistician

Rejected — envelope/literal checks would miss this. ``limit`` is valid, the
body is small, every leaf string is well typed, and the query is still illegal
(empty AND, extra operator key, unbounded cost):

    {
      "limit": 10,
      "query": {
        "and": [],
        "or": [{"field": "technology", "value": "python"}]
      }
    }

    NOT(NOT(NOT(...)))     depth 17   -> 422  (cap is 16; root is level 1)
    OR(leaf x 128)         129 nodes  -> 422  (cap is 128; OR counts too)

Why 16 and 128
    PRD required documented size/depth bounds before implementation.
    Architecture v2 proposed these conservative caps; they are not fitted to a
    measured query or table size. The assignment examples are tiny (Apple AND
    .net is 3 nodes at depth 2).

    16 levels  Enough nested Boolean, well below Python / JSON-container /
               SQL-nesting recursion danger. HTTP JSON decoding later uses a
               64-container guard, so over-deep queries still fail here as 422.
    128 nodes  Each node becomes SQL against unindexed relationship tables;
               set algebra may rescan those tables for repeated leaves. 128
               still allows a wide OR of many names.

    16,384-byte body and 256-character values bound payload size. Depth and
    node counts bound evaluation-graph size. Validation is iterative so these
    caps, rather than CPython's recursion limit, are the failing edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

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
    """Signal invalid input without including client-provided values or keys."""

    def __init__(self, message: str, path: str) -> None:
        """Store a safe explanation and a path composed of known schema members."""
        super().__init__(message)
        self.message = message
        self.path = path


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
    """
    envelope = _object(payload, "$", "Request must be an object.")
    if set(envelope) - {"query", "limit", "offset", "sort", "timeout_ms"}:
        raise QueryValidationError("Request contains unknown members.", "$")
    if "query" not in envelope:
        raise QueryValidationError("Request requires a query.", "$.query")

    sort = _object(envelope.get("sort", {}), "$.sort", "Sort must be an object.")
    if set(sort) - {"field", "direction"}:
        raise QueryValidationError("Sort contains unknown members.", "$.sort")
    sort_field = sort.get("field", "id")
    if not isinstance(sort_field, str) or sort_field not in (
        "id",
        "datetime_pulled",
        "job_title",
        "organization_name",
    ):
        raise QueryValidationError("Sort field is not supported.", "$.sort.field")
    sort_direction = sort.get("direction", "asc")
    if not isinstance(sort_direction, str) or sort_direction not in ("asc", "desc"):
        raise QueryValidationError("Sort direction must be asc or desc.", "$.sort.direction")

    options = SearchOptions(
        limit=_integer(envelope, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT),
        offset=_integer(envelope, "offset", 0, 0, MAX_OFFSET),
        sort_field=sort_field,
        sort_direction=sort_direction,
        timeout_ms=_integer(envelope, "timeout_ms", DEFAULT_TIMEOUT_MS, 1, MAX_TIMEOUT_MS),
    )
    return SearchRequest(query=_expression(envelope["query"]), options=options)


def _object(value: object, path: str, message: str) -> dict[str, object]:
    """Require an object, leaving unknown-key rejection to the schema owner."""
    if not isinstance(value, dict):
        raise QueryValidationError(message, path)
    return cast(dict[str, object], value)


def _integer(
    envelope: dict[str, object], name: str, default: int, minimum: int, maximum: int
) -> int:
    """Validate an integer control without accepting bool or coercing values."""
    value = envelope.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise QueryValidationError(
            f"Control must be an integer between {minimum} and {maximum}.", f"$.{name}"
        )
    return value


@dataclass(frozen=True)
class _Visit:
    """Hold a raw expression awaiting bounded validation."""

    value: object
    depth: int
    path: str


@dataclass(frozen=True)
class _Combine:
    """Build an operator after its children have been validated."""

    operator: Literal["and", "or", "not"]
    count: int


def _expression(value: object) -> Expression:
    """Build a tree iteratively with at most 128 scheduled expression nodes."""
    pending: list[_Visit | _Combine] = [_Visit(value, 1, "$.query")]
    results: list[Expression] = []
    scheduled = 1
    while pending:
        task = pending.pop()
        if isinstance(task, _Combine):
            children = tuple(results[-task.count :])
            del results[-task.count :]
            if task.operator == "not":
                results.append(Negation(children[0]))
            elif task.operator == "and":
                results.append(Conjunction(children))
            else:
                results.append(Disjunction(children))
            continue

        if task.depth > MAX_QUERY_DEPTH:
            raise QueryValidationError("Query exceeds 16 expression levels.", task.path)
        node = _object(task.value, task.path, "Expression must be an object.")
        if set(node) == {"field", "value"}:
            field = node["field"]
            if not isinstance(field, str) or field not in (
                "technology",
                "job_function",
                "organization",
            ):
                raise QueryValidationError("Filter field is not supported.", f"{task.path}.field")
            literal = node["value"]
            if (
                not isinstance(literal, str)
                or len(literal) > MAX_VALUE_LENGTH
                or not literal.strip()
            ):
                raise QueryValidationError(
                    "Value must be a nonblank string of at most 256 characters.",
                    f"{task.path}.value",
                )
            results.append(Leaf(field, literal))
            continue

        if set(node) == {"not"}:
            operator = "not"
            children_raw = [node["not"]]
        elif set(node) in ({"and"}, {"or"}):
            operator = "and" if "and" in node else "or"
            raw = node[operator]
            if not isinstance(raw, list) or not raw:
                raise QueryValidationError(
                    "AND/OR requires a nonempty array.", f"{task.path}.{operator}"
                )
            children_raw = raw
        else:
            raise QueryValidationError("Expression has invalid or ambiguous members.", task.path)

        scheduled += len(children_raw)
        if scheduled > MAX_QUERY_NODES:
            raise QueryValidationError("Query exceeds 128 expression nodes.", task.path)
        pending.append(_Combine(operator, len(children_raw)))
        for index in range(len(children_raw) - 1, -1, -1):
            path = f"{task.path}.{operator}"
            if operator != "not":
                path += f"[{index}]"
            pending.append(_Visit(children_raw[index], task.depth + 1, path))
    return results[0]
