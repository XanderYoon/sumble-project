"""Behavioral coverage for bounded Boolean expressions and search controls."""

import copy
import socket

import pytest

from sumble_api import query


def leaf(field="technology", value="Python"):
    return {"field": field, "value": value}


@pytest.mark.parametrize("field", ["technology", "job_function", "organization"])
@pytest.mark.parametrize(
    "value", ["Apple", ".net", "psql", "  PyThOn  ", "O'Reilly_%\\", "é" * 256]
)
def test_leaf_preserves_literal_and_defaults(field, value):
    request = query.parse_query({"query": leaf(field, value)})
    assert request.query == query.Leaf(field, value)
    assert request.options == query.SearchOptions(10, 0, "id", "asc", 25000)


def test_nested_expression_preserves_child_order_and_input():
    payload = {
        "query": {
            "and": [
                leaf("organization", "apple"),
                {"not": {"or": [leaf("job_function", "statistician"), leaf("technology", "psql")]}},
            ]
        }
    }
    original = copy.deepcopy(payload)
    request = query.parse_query(payload)
    assert request.query == query.Conjunction(
        (
            query.Leaf("organization", "apple"),
            query.Negation(
                query.Disjunction(
                    (query.Leaf("job_function", "statistician"), query.Leaf("technology", "psql"))
                )
            ),
        )
    )
    assert payload == original


@pytest.mark.parametrize("operator,expected", [("and", "Conjunction"), ("or", "Disjunction")])
def test_single_child_arrays(operator, expected):
    assert query.parse_query({"query": {operator: [leaf()]}}).query == getattr(query, expected)(
        (query.Leaf("technology", "Python"),)
    )


@pytest.mark.parametrize(
    "payload", [None, False, 1, "query", [], {}, {"query": leaf(), "extra": 1}]
)
def test_reject_invalid_envelopes(payload):
    with pytest.raises(query.QueryValidationError):
        query.parse_query(payload)


@pytest.mark.parametrize(
    "node",
    [
        None,
        False,
        1,
        "python",
        [],
        {},
        {"field": "technology"},
        {"value": "Python"},
        {"field": "technology", "value": "Python", "extra": True},
        {"and": [], "or": []},
        {"not": leaf(), "and": [leaf()]},
        {"and": [leaf()], "field": "technology", "value": "Python"},
        {"AND": [leaf()]},
        {"unknown": leaf()},
        {"not": []},
        {"not": [leaf()]},
        {"not": None},
    ],
)
def test_reject_invalid_expression_shapes(node):
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": node})


@pytest.mark.parametrize("operator", ["and", "or"])
@pytest.mark.parametrize("children", [[], None, {}, "python", False, 3, [None], [leaf(), {}]])
def test_reject_invalid_boolean_arrays(operator, children):
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": {operator: children}})


@pytest.mark.parametrize("field", [None, False, 1, [], {}, "Technology", "id", ""])
def test_reject_invalid_fields(field):
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": leaf(field=field)})


@pytest.mark.parametrize("value", [None, False, 1, 1.0, [], {}, "", " \t\r\n", "\u2003", "x" * 257])
def test_reject_invalid_literals(value):
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": leaf(value=value)})


def nested_not(depth):
    node = leaf()
    for _ in range(depth - 1):
        node = {"not": node}
    return node


def test_exact_depth_boundary():
    expression = query.parse_query({"query": nested_not(16)}).query
    for _ in range(15):
        assert isinstance(expression, query.Negation)
        expression = expression.child
    assert expression == query.Leaf("technology", "Python")


@pytest.mark.parametrize("depth", [17, 10000])
def test_excess_depth_does_not_recurse(depth):
    with pytest.raises(query.QueryValidationError, match="16 expression levels"):
        query.parse_query({"query": nested_not(depth)})


def test_cycle_is_bounded():
    node = {}
    node["not"] = node
    with pytest.raises(query.QueryValidationError, match="16 expression levels"):
        query.parse_query({"query": node})


@pytest.mark.parametrize("operator", ["and", "or"])
def test_node_count_boundary_includes_operator(operator):
    request = query.parse_query({"query": {operator: [leaf()] * 127}})
    assert isinstance(request.query, query.Conjunction | query.Disjunction)
    assert len(request.query.children) == 127
    with pytest.raises(query.QueryValidationError, match="128 expression nodes"):
        query.parse_query({"query": {operator: [leaf()] * 128}})


def test_node_count_is_global_across_branches():
    expression = {"and": [{"or": [leaf()] * 63}, {"not": {"or": [leaf()] * 61}}]}
    query.parse_query({"query": expression})  # 128 nodes, including all four operators.
    expression["and"][1]["not"]["or"].append(leaf())
    with pytest.raises(query.QueryValidationError, match="128 expression nodes"):
        query.parse_query({"query": expression})


@pytest.mark.parametrize(
    "control,minimum,maximum", [("limit", 1, 10), ("offset", 0, 100000), ("timeout_ms", 1, 25000)]
)
def test_control_boundaries(control, minimum, maximum):
    for value in (minimum, maximum):
        assert (
            getattr(query.parse_query({"query": leaf(), control: value}).options, control) == value
        )
    for value in (minimum - 1, maximum + 1, True, False, 1.0, "1", None, [], {}):
        with pytest.raises(query.QueryValidationError) as error:
            query.parse_query({"query": leaf(), control: value})
        assert error.value.path == f"$.{control}"


@pytest.mark.parametrize("field", ["id", "datetime_pulled", "job_title", "organization_name"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sort_allowlist(field, direction):
    options = query.parse_query(
        {"query": leaf(), "sort": {"field": field, "direction": direction}}
    ).options
    assert (options.sort_field, options.sort_direction) == (field, direction)


@pytest.mark.parametrize(
    "sort,field,direction",
    [
        ({}, "id", "asc"),
        ({"field": "job_title"}, "job_title", "asc"),
        ({"direction": "desc"}, "id", "desc"),
    ],
)
def test_sort_members_default_independently(sort, field, direction):
    options = query.parse_query({"query": leaf(), "sort": sort}).options
    assert (options.sort_field, options.sort_direction) == (field, direction)


@pytest.mark.parametrize(
    "sort",
    [
        None,
        [],
        "id",
        False,
        1,
        {"extra": "asc"},
        {"field": None},
        {"field": []},
        {"field": "technology"},
        {"field": "ID"},
        {"direction": None},
        {"direction": {}},
        {"direction": "ASC"},
        {"direction": "invalid"},
    ],
)
def test_reject_invalid_sort(sort):
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": leaf(), "sort": sort})


@pytest.mark.parametrize(
    "payload,path",
    [
        # Overlong leaf. Path names the slot; the 256-char canary stays out of the error.
        ({"query": {"and": [leaf(), leaf(value="sensitive" * 40)]}}, "$.query.and[1].value"),
        ({"query": leaf(field="sensitive")}, "$.query.field"),  # unknown field name
        ({"query": {"sensitive": leaf()}}, "$.query"),  # unknown expression key
        ({"query": leaf(), "sensitive": True}, "$"),  # unknown envelope key
        ({"query": leaf(), "sort": {"field": "sensitive"}}, "$.sort.field"),
        ({"query": leaf(), "sort": {"sensitive": True}}, "$.sort"),  # unknown sort key
        ({"query": leaf(), "limit": "sensitive"}, "$.limit"),  # non-integer control
    ],
)
def test_diagnostics_do_not_echo_input(payload, path):
    """422 diagnostics use a schema path and a fixed message; they never echo input.

    "sensitive" is a canary in the illegal field, key, or value. The exception
    string, repr, and message must omit it so HTTP bodies and non-200 logs cannot
    leak rejected literals. str(error) is the safe message alone.
    """
    with pytest.raises(query.QueryValidationError) as error:
        query.parse_query(payload)
    assert error.value.path == path
    assert "sensitive" not in str(error.value)
    assert "sensitive" not in repr(error.value)
    assert error.value.message == str(error.value)


def test_parsing_needs_no_database_or_network(monkeypatch):
    """parse_query is pure: JSON in, tree or QueryValidationError out.

    T001 must not open sockets or Postgres. If validation ever called
    socket.socket or psycopg.connect (for example to check that a name exists),
    this fails immediately. SQL starts in later tickets.
    """

    def forbidden(*args, **kwargs):
        pytest.fail("Parsing attempted network/database work")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("psycopg.connect", forbidden)
    assert query.parse_query({"query": leaf()}).query == query.Leaf("technology", "Python")
