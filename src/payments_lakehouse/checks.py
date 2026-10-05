"""Run the reconciliation SQL files and fail loudly if any check is false.

Each file in sql/checks/ returns one row per check: (check_name, expected, actual, passed). The
scheduled job runs this as its last task, so a failed check fails the job and sends the alert
email. The logic is plain Python, so it is tested without Spark; only `main` needs a Spark session.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# SQL text in, rows out. In the job this is `lambda sql: spark.sql(sql).collect()`.
Executor = Callable[[str], Sequence[Sequence]]


@dataclass(frozen=True)
class CheckResult:
    suite: str
    name: str
    expected: object
    actual: object
    passed: bool


def strip_comments(sql: str) -> str:
    """Drop full-line `--` comments so the file can be sent as one statement."""
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def _as_bool(value: object) -> bool:
    # A SQL warehouse returns the text "true"; Spark returns a real boolean.
    return value is True or str(value).lower() == "true"


def run_suite(path: Path, execute: Executor) -> list[CheckResult]:
    rows = execute(strip_comments(path.read_text()))
    if not rows:
        # A suite that returns nothing would look like a pass, so it is an error.
        raise RuntimeError(f"{path.name} returned no checks")
    return [CheckResult(path.stem, r[0], r[1], r[2], _as_bool(r[3])) for r in rows]


def run_all(directory: Path, execute: Executor) -> list[CheckResult]:
    suites = sorted(directory.glob("*_reconciliation.sql"))
    if not suites:
        raise RuntimeError(f"no *_reconciliation.sql files found in {directory}")
    results = []
    for suite in suites:
        results.extend(run_suite(suite, execute))
    return results


def _plain(value: object) -> str:
    # The SQL widens whole numbers to decimals, so a count would print as "1097.00".
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    return str(value)


def failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed]


def format_report(results: list[CheckResult]) -> str:
    bad = failures(results)
    lines = [f"{len(results) - len(bad)} of {len(results)} checks passed"]
    for r in bad:
        lines.append(
            f"FAILED [{r.suite}] {r.name}: expected {_plain(r.expected)}, actual {_plain(r.actual)}"
        )
    return "\n".join(lines)


def split_statements(script: str) -> list[str]:
    """Split a SQL file into statements on `;` after dropping full-line comments."""
    return [part.strip() for part in strip_comments(script).split(";") if part.strip()]


def result_rows(results: list[CheckResult], run_at: datetime, run_id: str | None) -> list[tuple]:
    """The check history rows: one per check, all stamped with the same run time."""
    return [
        (run_at, run_id, r.suite, r.name, _plain(r.expected), _plain(r.actual), r.passed)
        for r in results
    ]


CHECK_SCHEMA = (
    "run_at TIMESTAMP, run_id STRING, suite STRING, check_name STRING, "
    "expected STRING, actual STRING, passed BOOLEAN"
)
EXPECTATION_SCHEMA = (
    "run_at TIMESTAMP, run_id STRING, update_id STRING, dataset STRING, "
    "expectation STRING, passed_records BIGINT, failed_records BIGINT"
)


def record(
    spark,
    results: list[CheckResult],
    quality_dir: Path,
    run_id: str | None = None,
    pipeline_id: str | None = None,
    run_at: datetime | None = None,
) -> None:
    """Append this run to the history in workspace.quality (ADR-027).

    Creates the schema, tables and views first (idempotent), then appends the check results, and
    the pipeline's expectation metrics when a pipeline id is given.
    """
    run_at = run_at or datetime.now(UTC)
    for statement in split_statements((quality_dir / "setup.sql").read_text()):
        spark.sql(statement)
    spark.createDataFrame(result_rows(results, run_at, run_id), CHECK_SCHEMA).write.mode(
        "append"
    ).saveAsTable("workspace.quality.check_results")
    if pipeline_id:
        snapshot = (quality_dir / "expectation_snapshot.sql").read_text()
        rows = [
            (run_at, run_id, *tuple(row))
            for row in spark.sql(
                strip_comments(snapshot).replace("{pipeline_id}", pipeline_id)
            ).collect()
        ]
        spark.createDataFrame(rows, EXPECTATION_SCHEMA).write.mode("append").saveAsTable(
            "workspace.quality.expectation_results"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the reconciliation SQL checks.")
    parser.add_argument("--checks-dir", type=Path, required=True)
    parser.add_argument(
        "--quality-dir",
        type=Path,
        help="folder with setup.sql; when given, the results are appended to workspace.quality",
    )
    parser.add_argument("--run-id", help="the job run id, stored with every result")
    parser.add_argument("--pipeline-id", help="also store the pipeline's expectation metrics")
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession  # only available on Databricks compute

    spark = SparkSession.builder.getOrCreate()
    results = run_all(args.checks_dir, lambda sql: spark.sql(sql).collect())
    report = format_report(results)
    print(report)
    if args.quality_dir:
        # The history is a convenience: if writing it fails, say so loudly but still let the
        # checks decide whether the job passes (below), so a failed check is never hidden.
        try:
            record(spark, results, args.quality_dir, args.run_id, args.pipeline_id)
        except Exception as error:
            print(f"WARNING: could not record the results in workspace.quality: {error!r}")
    if failures(results):
        raise SystemExit(report)  # a non-zero exit fails the job task


if __name__ == "__main__":
    main()
