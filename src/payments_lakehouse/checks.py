"""Run the reconciliation SQL files and fail loudly if any check is false.

Each file in sql/checks/ returns one row per check: (check_name, expected, actual, passed). The
scheduled job runs this as its last task, so a failed check fails the job and sends the alert
email. The logic is plain Python, so it is tested without Spark; only `main` needs a Spark session.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the reconciliation SQL checks.")
    parser.add_argument("--checks-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession  # only available on Databricks compute

    spark = SparkSession.builder.getOrCreate()
    results = run_all(args.checks_dir, lambda sql: spark.sql(sql).collect())
    report = format_report(results)
    print(report)
    if failures(results):
        raise SystemExit(report)  # a non-zero exit fails the job task


if __name__ == "__main__":
    main()
