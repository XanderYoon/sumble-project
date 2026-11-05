"""Core parameterized SQL compilation tests.

Coverage gaps:
- job_function and organization join/dictionary fragments
- Independent relationship joins for two technology leaves
- Double NOT grouping against job_posts
- Single-child AND/OR wrapping
- Repeated leaves keep one parameter per %s placeholder
- UNION vs UNION ALL
"""

import pytest

from sumble_api import compiler, query


def compile_expression(expression):
    return compiler.compile_query(query.parse_query({"query": expression}).query)


def leaf(field, value):
    return {"field": field, "value": value}


def test_technology_leaf_is_parameterized_exact_match():
    compiled = compile_expression(leaf("technology", " AppLE "))
    assert "FROM job_posts_tech AS association" in compiled.sql
    assert "JOIN tech AS dictionary ON dictionary.id = association.tech_id" in compiled.sql
    assert "WHERE lower(dictionary.name) = lower(%s)" in compiled.sql
    assert compiled.parameters == (" AppLE ",)
    assert compiled.sql.startswith("(") and compiled.sql.endswith(")")


def test_literal_values_never_change_sql():
    baseline = compile_expression(leaf("technology", "baseline"))
    compiled = compile_expression(leaf("technology", "x'); DROP TABLE job_posts; --"))
    assert compiled.sql == baseline.sql
    assert "DROP TABLE" not in compiled.sql
    assert compiled.parameters == ("x'); DROP TABLE job_posts; --",)


def test_nested_operators_preserve_grouping_and_parameter_order():
    apple = leaf("organization", "Apple")
    python = leaf("technology", "python")
    sql = leaf("technology", "sql")
    engineering = leaf("job_function", "Engineering")
    expression = {"and": [apple, {"not": {"or": [python, {"and": [sql, engineering]}]}}]}
    compiled = compile_expression(expression)
    expected_child = (
        f"({compile_expression(python).sql} UNION "
        f"({compile_expression(sql).sql} INTERSECT {compile_expression(engineering).sql}))"
    )
    assert compiled.sql == (
        f"({compile_expression(apple).sql} INTERSECT "
        f"(SELECT id FROM job_posts EXCEPT {expected_child}))"
    )
    assert compiled.parameters == ("Apple", "python", "sql", "Engineering")


def test_compilation_never_opens_a_database_connection(monkeypatch):
    import psycopg

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Pure SQL compilation attempted a database connection")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    compiled = compile_expression({"not": leaf("organization", "Apple")})
    assert compiled.parameters == ("Apple",)
    assert "EXCEPT" in compiled.sql
