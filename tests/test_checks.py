from decimal import Decimal

import pytest

from payments_lakehouse.checks import (
    failures,
    format_report,
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
