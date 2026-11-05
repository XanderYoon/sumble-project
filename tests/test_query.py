"""Core Boolean parse and search-control tests.

Coverage gaps:
- Leaf preservation across all three fields and punctuation, Unicode, padded, and 256-char literals
- Single-child AND/OR wrapping
- Full invalid envelope matrix (None, bool, number, string, list, empty object)
- Full invalid expression shapes (missing members, mixed operators, uppercase AND, NOT arrays/null)
- Invalid AND/OR child arrays (empty, non-list, nested invalid nodes)
- Invalid field names (wrong case, id, empty, non-string)
- Invalid literals (None, bool, numbers, whitespace-only, 257 characters)
- Self-referential cycle detection
- Node-count accounting across mixed AND/OR/NOT branches
- Offset and timeout_ms bounds plus bool/float/string/null integer rejection
- Full sort allowlist (four fields x two directions) and independent member defaults
- Invalid sort objects (unknown keys, wrong case, non-objects)
- Diagnostic JSON-pointer coverage for every schema location
"""

import copy
import socket

import pytest

from sumble_api import query


def leaf(field="technology", value="Python"):
    return {"field": field, "value": value}


def test_nested_expression_preserves_child_order_and_defaults():
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
    assert request.options == query.SearchOptions(10, 0, "id", "asc", 25000)
    assert payload == original


def test_reject_empty_and_and_unknown_envelope_key():
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": {"and": []}})
    with pytest.raises(query.QueryValidationError):
        query.parse_query({"query": leaf(), "extra": 1})


def test_depth_and_node_caps():
    node = leaf()
    for _ in range(15):
        node = {"not": node}
    expression = query.parse_query({"query": node}).query
    for _ in range(15):
        assert isinstance(expression, query.Negation)
        expression = expression.child
    assert expression == query.Leaf("technology", "Python")

    too_deep = {"not": node}
    with pytest.raises(query.QueryValidationError, match="16 expression levels"):
        query.parse_query({"query": too_deep})

    query.parse_query({"query": {"or": [leaf()] * 127}})
    with pytest.raises(query.QueryValidationError, match="128 expression nodes"):
        query.parse_query({"query": {"or": [leaf()] * 128}})


def test_limit_bounds_and_sort_allowlist():
    assert query.parse_query({"query": leaf(), "limit": 1}).options.limit == 1
    assert query.parse_query({"query": leaf(), "limit": 10}).options.limit == 10
    with pytest.raises(query.QueryValidationError) as error:
        query.parse_query({"query": leaf(), "limit": 0})
    assert error.value.path == "$.limit"
    options = query.parse_query(
        {"query": leaf(), "sort": {"field": "job_title", "direction": "desc"}}
    ).options
    assert (options.sort_field, options.sort_direction) == ("job_title", "desc")


def test_diagnostics_do_not_echo_input():
    with pytest.raises(query.QueryValidationError) as error:
        query.parse_query({"query": leaf(value="sensitive" * 40)})
    assert error.value.path == "$.query.value"
    assert "sensitive" not in str(error.value)
    assert "sensitive" not in repr(error.value)
    assert error.value.message == str(error.value)


def test_parsing_needs_no_database_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Parsing attempted network/database work")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("psycopg.connect", forbidden)
    assert query.parse_query({"query": leaf()}).query == query.Leaf("technology", "Python")
