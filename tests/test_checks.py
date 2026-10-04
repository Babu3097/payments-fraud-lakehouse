import sys
import types
from decimal import Decimal

import pytest

from payments_lakehouse.checks import (
    failures,
    format_report,
    main,
    run_all,
    run_suite,
    strip_comments,
)


def write_suite(directory, name, text="-- a comment\nSELECT 1"):
    path = directory / name
    path.write_text(text)
    return path


def test_strip_comments_drops_only_full_line_comments():
    sql = "-- header\nSELECT a -- inline stays\n  -- indented comment\nFROM t"
    assert strip_comments(sql) == "SELECT a -- inline stays\nFROM t"


def test_run_suite_sends_the_sql_without_comments_and_reads_booleans_of_both_kinds(tmp_path):
    path = write_suite(tmp_path, "demo_reconciliation.sql")
    seen = []

    def execute(sql):
        seen.append(sql)
        # A Spark Row gives a real boolean, a SQL warehouse gives text.
        return [
            ("rows match", 10, 10, True),
            ("amounts match", 5, 6, "false"),
            ("keys", 0, 0, "true"),
        ]

    results = run_suite(path, execute)
    assert seen == ["SELECT 1"]
    assert [r.passed for r in results] == [True, False, True]
    assert results[1].suite == "demo_reconciliation"
    assert (results[1].expected, results[1].actual) == (5, 6)


def test_a_suite_that_returns_no_rows_is_an_error_not_a_pass(tmp_path):
    path = write_suite(tmp_path, "empty_reconciliation.sql")
    with pytest.raises(RuntimeError, match="returned no checks"):
        run_suite(path, lambda sql: [])


def test_run_all_runs_every_reconciliation_file_in_order_and_ignores_the_rest(tmp_path):
    write_suite(tmp_path, "silver_reconciliation.sql")
    write_suite(tmp_path, "bronze_reconciliation.sql")
    write_suite(tmp_path, "notes.sql")
    order = []

    def execute(sql):
        order.append(len(order))
        return [("check", 1, 1, True)]

    results = run_all(tmp_path, execute)
    assert [r.suite for r in results] == ["bronze_reconciliation", "silver_reconciliation"]
    assert len(order) == 2


def test_run_all_without_any_suite_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="no .*_reconciliation.sql files"):
        run_all(tmp_path, lambda sql: [("check", 1, 1, True)])


def test_report_names_each_failed_check_with_both_values(tmp_path):
    path = write_suite(tmp_path, "gold_reconciliation.sql")
    results = run_suite(path, lambda sql: [("good", 1, 1, True), ("bad total", 100, 99, False)])
    assert len(failures(results)) == 1
    report = format_report(results)
    assert report.splitlines()[0] == "1 of 2 checks passed"
    assert "FAILED [gold_reconciliation] bad total: expected 100, actual 99" in report


def test_an_all_passing_report_has_no_failure_lines(tmp_path):
    path = write_suite(tmp_path, "gold_reconciliation.sql")
    results = run_suite(path, lambda sql: [("a", 1, 1, True), ("b", 2, 2, "true")])
    assert failures(results) == []
    assert format_report(results) == "2 of 2 checks passed"


def test_whole_numbers_print_without_decimals_but_real_decimals_keep_them(tmp_path):
    path = write_suite(tmp_path, "gold_reconciliation.sql")
    rows = [
        ("count", Decimal("1097.00"), Decimal("1096.00"), False),
        ("sum", Decimal("12.50"), 3, False),
    ]
    report = format_report(run_suite(path, lambda sql: rows))
    assert "count: expected 1097, actual 1096" in report
    assert "sum: expected 12.50, actual 3" in report


class FakeSpark:
    """Stands in for the Spark session of a Databricks task: records the SQL, returns set rows."""

    def __init__(self, rows):
        self.rows = rows
        self.sent = []

    def sql(self, text):
        self.sent.append(text)
        return self

    def collect(self):
        return self.rows


def use_fake_spark(monkeypatch, rows):
    spark = FakeSpark(rows)
    sql_module = types.ModuleType("pyspark.sql")
    sql_module.SparkSession = types.SimpleNamespace(
        builder=types.SimpleNamespace(getOrCreate=lambda: spark)
    )
    root = types.ModuleType("pyspark")
    root.sql = sql_module
    monkeypatch.setitem(sys.modules, "pyspark", root)
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql_module)
    return spark


def test_the_job_command_passes_quietly_when_every_check_is_true(tmp_path, monkeypatch, capsys):
    write_suite(tmp_path, "bronze_reconciliation.sql", "-- header\nSELECT 1")
    spark = use_fake_spark(monkeypatch, [("a", 1, 1, True), ("b", 2, 2, "true")])
    main(["--checks-dir", str(tmp_path)])  # returns: the task succeeds
    assert capsys.readouterr().out.strip() == "2 of 2 checks passed"
    assert spark.sent == ["SELECT 1"]  # the comment never reaches Spark


def test_the_job_command_exits_with_the_report_when_a_check_is_false(tmp_path, monkeypatch, capsys):
    # This is the line that fails the job and sends the alert email, so it is tested directly.
    write_suite(tmp_path, "gold_reconciliation.sql")
    use_fake_spark(
        monkeypatch, [("good", 1, 1, True), ("dim_date: days", Decimal("1097.00"), 1096, False)]
    )
    with pytest.raises(SystemExit) as stopped:
        main(["--checks-dir", str(tmp_path)])
    report = stopped.value.code
    assert isinstance(report, str)  # a message, so Python exits with status 1 and shows it
    assert report.splitlines() == [
        "1 of 2 checks passed",
        "FAILED [gold_reconciliation] dim_date: days: expected 1097, actual 1096",
    ]
    assert capsys.readouterr().out.startswith("1 of 2 checks passed")


def test_the_job_command_raises_when_there_is_nothing_to_check(tmp_path, monkeypatch):
    use_fake_spark(monkeypatch, [("a", 1, 1, True)])
    with pytest.raises(RuntimeError, match="no .*_reconciliation.sql files"):
        main(["--checks-dir", str(tmp_path)])


def test_the_job_command_needs_to_be_told_where_the_checks_are():
    with pytest.raises(SystemExit) as stopped:
        main([])
    assert stopped.value.code == 2  # argparse's own usage error
