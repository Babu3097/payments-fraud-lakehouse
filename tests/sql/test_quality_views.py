"""The data quality summary: the setup script runs and its views summarise the history."""

from datetime import datetime
from pathlib import Path

import pytest

from payments_lakehouse.checks import split_statements
from tests.helpers.lakeflow_sql import localize

pytestmark = pytest.mark.sql

SETUP = Path(__file__).resolve().parents[2] / "sql/quality/setup.sql"
T1 = datetime(2026, 10, 5, 6, 0)
T2 = datetime(2026, 10, 6, 6, 0)


def build(spark, fresh=True):
    if fresh:  # saved tables outlive a test (they live in the shared warehouse), so start clean
        for view in ("check_scorecard", "check_history", "expectation_scorecard"):
            spark.sql(f"DROP VIEW IF EXISTS quality_{view}")
        for table in ("check_results", "expectation_results"):
            spark.sql(f"DROP TABLE IF EXISTS quality_{table}")
    # CREATE SCHEMA has no local equivalent (the names are flattened to plain tables).
    for statement in split_statements(SETUP.read_text()):
        if not statement.startswith("CREATE SCHEMA"):
            spark.sql(localize(statement))


def add_checks(spark, rows):
    spark.createDataFrame(
        rows,
        "run_at TIMESTAMP, run_id STRING, suite STRING, check_name STRING, "
        "expected STRING, actual STRING, passed BOOLEAN",
    ).write.mode("append").saveAsTable("quality_check_results")


def test_the_setup_script_can_run_twice(spark):
    build(spark)
    build(spark, fresh=False)  # idempotent: the job runs it every day


def test_the_scorecard_shows_only_the_newest_run_split_by_suite(spark):
    build(spark)
    add_checks(
        spark,
        [
            (T1, "1", "silver", "a", "1", "1", True),
            (T1, "1", "silver", "b", "2", "3", False),
            (T2, "2", "silver", "a", "1", "1", True),
            (T2, "2", "silver", "b", "2", "2", True),
            (T2, "2", "gold", "c", "5", "5", True),
        ],
    )
    rows = {r.suite: r for r in spark.table("quality_check_scorecard").collect()}
    assert set(rows) == {"silver", "gold"}
    assert (rows["silver"].checks, rows["silver"].passed, rows["silver"].failed) == (2, 2, 0)
    assert rows["gold"].run_at == T2


def test_the_history_has_one_row_per_run_so_a_failure_shows_on_its_day(spark):
    build(spark)
    add_checks(
        spark,
        [
            (T1, "1", "silver", "a", "1", "1", True),
            (T1, "1", "silver", "b", "2", "3", False),
            (T2, "2", "silver", "a", "1", "1", True),
        ],
    )
    history = [
        (r.run_at, r.checks, r.passed, r.failed)
        for r in spark.table("quality_check_history").orderBy("run_at").collect()
    ]
    assert history == [(T1, 2, 1, 1), (T2, 1, 1, 0)]


def test_the_expectation_scorecard_uses_the_newest_snapshot_and_a_failure_rate(spark):
    build(spark)
    spark.createDataFrame(
        [
            (T1, "1", "u1", "silver.t", "old_rule", 5, 5),
            (T2, "2", "u2", "silver.t", "has_id", 90, 10),
            (T2, "2", "u2", "silver.t", "never_ran", 0, 0),
        ],
        "run_at TIMESTAMP, run_id STRING, update_id STRING, dataset STRING, "
        "expectation STRING, passed_records BIGINT, failed_records BIGINT",
    ).write.mode("append").saveAsTable("quality_expectation_results")
    rows = {r.expectation: r for r in spark.table("quality_expectation_scorecard").collect()}
    assert set(rows) == {"has_id", "never_ran"}
    assert rows["has_id"].failure_rate == pytest.approx(0.1)
    assert rows["never_ran"].failure_rate is None  # no rows seen: not a 0% or a 100%, just unknown


def test_the_views_are_empty_before_any_run(spark):
    build(spark)
    assert spark.table("quality_check_scorecard").count() == 0
    assert spark.table("quality_check_history").count() == 0
    assert spark.table("quality_expectation_scorecard").count() == 0
