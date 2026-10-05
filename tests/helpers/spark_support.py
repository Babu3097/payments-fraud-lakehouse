"""Local Spark for the SQL tests: find a Java, and build fixture tables from the real contracts.

The bronze loaders in pipelines/bronze/ declare each table's schema as a string. Fixtures read that
same string, so a test table cannot drift away from what production really ingests.
"""

import ast
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from tests.helpers.lakeflow_sql import PIPELINES, find, load, local_name, localize

SUPPORTED_JAVA = range(17, 22)  # Spark 4.0 runs on Java 17 and 21


def _java_major(java_home: Path) -> int | None:
    java = java_home / "bin" / "java"
    if not java.exists():
        return None
    try:
        result = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'version "(\d+)', result.stderr + result.stdout)
    return int(match.group(1)) if result.returncode == 0 and match else None


def find_java_home() -> str | None:
    """A Java 17 to 21 home, or None. The macOS /usr/bin/java stub is not a Java, and is skipped."""
    candidates = [os.environ.get("JAVA_HOME"), os.environ.get("JAVA_HOME_17_X64")]
    on_path = shutil.which("java")
    if on_path:
        candidates.append(str(Path(on_path).resolve().parent.parent))
    home = Path.home() / ".local"
    for pattern in ("jdk-*/Contents/Home", "jdk-*"):
        candidates += [str(p) for p in sorted(home.glob(pattern), reverse=True)]
    for candidate in candidates:
        if candidate and _java_major(Path(candidate)) in SUPPORTED_JAVA:
            return candidate
    return None


def contract(bronze_file: str, constant: str) -> str:
    """The schema string a bronze loader declares, for example ("paysim_transactions", "SCHEMA")."""
    tree = ast.parse((PIPELINES / "bronze" / f"{bronze_file}.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == constant for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise KeyError(f"{constant} not found in {bronze_file}.py")


def _as_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def make_table(spark, name: str, ddl: str, rows: list[dict]):
    """Register rows as a temp view named the way localize() renames the table.

    Values are written as text and cast to the declared types by Spark, which is what the
    contract does with real files. A key that is not a column is an error, so a misspelt column
    in a test cannot silently become a null.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    schema = StructType.fromDDL(ddl)
    columns = [field.name for field in schema.fields]
    for row in rows:
        unknown = set(row) - set(columns)
        if unknown:
            raise KeyError(f"{name}: not columns of the fixture: {sorted(unknown)}")
    text_schema = StructType([StructField(column, StringType()) for column in columns])
    data = [
        tuple(None if row.get(column) is None else _as_text(row[column]) for column in columns)
        for row in rows
    ]
    typed = spark.createDataFrame(data, text_schema).select(
        [F.col(f.name).cast(f.dataType).alias(f.name) for f in schema.fields]
    )
    typed.createOrReplaceTempView(local_name(name))
    return typed


def run_query(spark, file: str, name: str) -> list[dict]:
    """Run the query of one statement in a pipeline file, on whatever tables are registered."""
    statement = find(load(file), name)
    if statement.query is None:
        raise ValueError(f"{name} in {file} has no query")
    return [row.asDict(recursive=True) for row in spark.sql(localize(statement.query)).collect()]


def register_query(spark, file: str, name: str, as_view: str) -> None:
    """Run a statement's query and keep the result as a temp view, so the next step can read it."""
    statement = find(load(file), name)
    spark.sql(localize(statement.query)).createOrReplaceTempView(as_view)


def violations(spark, view: str, expectation) -> list[dict]:
    """The rows of a view that break an expectation, counted the way a pipeline counts them: a
    condition that is false or NULL is a violation."""
    sql = f"SELECT * FROM {view} WHERE NOT coalesce(({expectation.condition}), false)"
    return [row.asDict(recursive=True) for row in spark.sql(sql).collect()]
