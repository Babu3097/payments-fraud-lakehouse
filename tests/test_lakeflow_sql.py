"""Tests for the reader that strips Lakeflow wrappers off the pipeline SQL (tests/helpers).

They are plain Python, so they run everywhere. The SQL tests in tests/sql rely on this reader being
right, so it is tested first, including the awkward cases: a semicolon or the word AS inside a
string, a comment between statements, and commas inside an expectation.
"""

import pytest

from tests.helpers.lakeflow_sql import (
    PIPELINES,
    Expectation,
    find,
    local_name,
    localize,
    parse,
)

TABLE = """
-- A comment with a semicolon; and the word AS, which must not matter.
CREATE OR REFRESH STREAMING TABLE workspace.silver.things (
    CONSTRAINT id_present EXPECT (id IS NOT NULL) ON VIOLATION FAIL UPDATE,
    CONSTRAINT known_kind EXPECT (kind IN ('A', 'B')) ON VIOLATION DROP ROW,
    CONSTRAINT soft_check EXPECT (
        amount BETWEEN 0 AND 10
    )
)
COMMENT 'Rows AS received; nothing dropped'
AS
SELECT id, kind, amount
FROM STREAM (workspace.bronze.things_raw) -- trailing comment
WHERE id <> 'x;y';
"""


def test_a_table_gives_back_its_query_and_its_expectations():
    (statement,) = parse(TABLE)
    assert (statement.kind, statement.name) == ("table", "workspace.silver.things")
    assert statement.query.startswith("SELECT id, kind, amount")
    assert statement.query.endswith("WHERE id <> 'x;y'")
    assert "trailing comment" not in statement.query
    assert statement.expectations == (
        Expectation("id_present", "id IS NOT NULL", "FAIL UPDATE"),
        Expectation("known_kind", "kind IN ('A', 'B')", "DROP ROW"),
        Expectation("soft_check", "amount BETWEEN 0 AND 10", "WARN"),
    )


def test_a_semicolon_or_as_inside_a_string_does_not_split_or_start_the_query():
    # The COMMENT string above holds both. If either were taken as syntax the query would be wrong.
    (statement,) = parse(TABLE)
    assert "nothing dropped" not in statement.query


def test_a_table_with_no_query_is_still_a_statement_with_expectations():
    sql = "CREATE OR REFRESH STREAMING TABLE t (CONSTRAINT c EXPECT (a > 0)) COMMENT 'x';"
    (statement,) = parse(sql)
    assert statement.query is None
    assert statement.expectations == (Expectation("c", "a > 0", "WARN"),)


def test_a_private_table_with_a_column_list_has_no_query_and_no_expectations():
    (statement,) = parse("CREATE OR REFRESH PRIVATE STREAMING TABLE t (a STRING, b INT);")
    assert (statement.kind, statement.query, statement.expectations) == ("table", None, ())
    assert statement.columns == "a STRING, b INT"


def test_a_table_with_constraints_has_no_column_list():
    (statement,) = parse(TABLE)
    assert statement.columns is None


def test_an_insert_flow_gives_back_its_target_and_query():
    sql = """CREATE FLOW f AS INSERT INTO target BY NAME
    WITH h AS (SELECT 1 AS x)
    SELECT x FROM h;"""
    (statement,) = parse(sql)
    assert (statement.kind, statement.name, statement.target) == ("flow", "f", "target")
    assert statement.query.startswith("WITH h AS")


def test_an_auto_cdc_flow_is_recognised_and_has_no_query():
    sql = "CREATE FLOW f AS AUTO CDC INTO t FROM STREAM (s) KEYS (id) SEQUENCE BY ts;"
    (statement,) = parse(sql)
    assert (statement.kind, statement.query) == ("auto_cdc", None)


def test_a_temporary_view_and_several_statements_in_one_file():
    sql = """CREATE TEMPORARY VIEW v AS
    SELECT * -- noqa: AM04
    FROM STREAM (src)
    WHERE size(failed) = 0;
    CREATE OR REFRESH MATERIALIZED VIEW workspace.gold.m AS SELECT 1 AS one"""
    first, second = parse(sql)
    assert (first.kind, first.name) == ("view", "v")
    assert "noqa" not in first.query
    assert (second.kind, second.name, second.query) == (
        "view",
        "workspace.gold.m",
        "SELECT 1 AS one",
    )


def test_find_accepts_a_full_or_a_short_name():
    statements = parse(TABLE)
    assert find(statements, "things") is find(statements, "workspace.silver.things")
    with pytest.raises(KeyError):
        find(statements, "missing")


def test_localize_drops_stream_and_flattens_three_part_names():
    query = "SELECT * FROM STREAM (workspace.bronze.a) JOIN workspace.silver.b ON 1 = 1 JOIN c"
    assert localize(query) == "SELECT * FROM bronze_a JOIN silver_b ON 1 = 1 JOIN c"
    assert localize("FROM STREAM(transactions_unified)") == "FROM transactions_unified"


def test_local_name_matches_what_localize_produces():
    assert local_name("workspace.silver.transactions") == "silver_transactions"
    assert local_name("transactions_unified") == "transactions_unified"


SQL_FILES = sorted(PIPELINES.glob("*/*.sql"))


def test_the_sweep_finds_the_pipeline_files():
    assert len(SQL_FILES) >= 10


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_statement_in_every_pipeline_file_is_understood(path):
    statements = parse(path.read_text())
    assert statements, f"{path.name} has no statements"
    assert [s.name for s in statements if s.kind == "other"] == []
    for statement in statements:
        if statement.kind in {"flow", "view"} or (statement.kind == "table" and statement.query):
            assert statement.query, f"{statement.name} has no query"
