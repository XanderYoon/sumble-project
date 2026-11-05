"""Verify pure, parameterized SQL compilation and Boolean grouping."""

import pytest

from sumble_api import compiler, query


def compile_expression(expression):
    return compiler.compile_query(query.parse_query({"query": expression}).query)


def leaf(field, value):
    return {"field": field, "value": value}


@pytest.mark.parametrize(
    ("field", "relationship", "dictionary", "join"),
    [
        (
            "technology",
            "job_posts_tech AS association",
            "tech AS dictionary",
            "dictionary.id = association.tech_id",
        ),
        (
            "job_function",
            "job_posts_job_functions AS association",
            "job_functions AS dictionary",
            "dictionary.id = association.job_function_id",
        ),
        (
            "organization",
            "job_posts AS job",
            "organizations AS dictionary",
            "dictionary.id = job.organization_id",
        ),
    ],
)
def test_leaves_use_normalized_joins_and_bound_exact_names(field, relationship, dictionary, join):
    compiled = compile_expression(leaf(field, " AppLE "))

    assert f"FROM {relationship}" in compiled.sql
    assert f"JOIN {dictionary} ON {join}" in compiled.sql
    assert "WHERE lower(dictionary.name) = lower(%s)" in compiled.sql
    assert "SELECT DISTINCT " in compiled.sql
    assert compiled.parameters == (" AppLE ",)
    assert compiled.sql.startswith("(") and compiled.sql.endswith(")")


@pytest.mark.parametrize(
    "value",
    ["O'Reilly", "%", ".net", "psql", "x'); DROP TABLE job_posts; --", "a_b", "Straße"],
)
def test_literal_values_never_change_sql(value):
    baseline = compile_expression(leaf("technology", "baseline"))
    compiled = compile_expression(leaf("technology", value))

    assert compiled.sql == baseline.sql
    assert compiled.sql.count("%s") == 1
    assert compiled.parameters == (value,)


def test_nested_operators_preserve_complete_child_grouping_and_parameter_order():
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
    assert compile_expression(expression) == compiled


def test_separate_technology_leaves_have_independent_relationship_joins():
    compiled = compile_expression(
        {"and": [leaf("technology", "python"), leaf("technology", "sql")]}
    )

    assert compiled.sql.count("FROM job_posts_tech AS association") == 2
    assert " INTERSECT " in compiled.sql
    assert compiled.parameters == ("python", "sql")


def test_negation_complements_actual_jobs_and_groups_nested_negation():
    child = leaf("job_function", "Engineering")
    compiled = compile_expression({"not": {"not": child}})

    assert compiled.sql == (
        "(SELECT id FROM job_posts EXCEPT "
        f"(SELECT id FROM job_posts EXCEPT {compile_expression(child).sql}))"
    )
    assert compiled.parameters == ("Engineering",)
    assert "NOT IN" not in compiled.sql


@pytest.mark.parametrize("operator", ["and", "or"])
def test_single_child_operators_are_valid_grouped_sets(operator):
    child = leaf("organization", "Apple")
    compiled = compile_expression({operator: [child]})

    assert compiled.sql == f"({compile_expression(child).sql})"
    assert compiled.parameters == ("Apple",)


def test_repeated_leaves_keep_one_parameter_per_placeholder():
    child = leaf("technology", "python")
    compiled = compile_expression({"or": [child, child]})

    assert " UNION " in compiled.sql
    assert "UNION ALL" not in compiled.sql
    assert compiled.sql.count("%s") == 2
    assert compiled.parameters == ("python", "python")


def test_compilation_never_opens_a_database_connection(monkeypatch):
    import psycopg

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Pure SQL compilation attempted a database connection")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    compiled = compile_expression({"not": leaf("organization", "Apple")})

    assert compiled.parameters == ("Apple",)
