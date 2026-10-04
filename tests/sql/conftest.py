"""Fixtures for the SQL tests: one local Spark session, and a clean slate for every test."""

import os
import time

import pytest

from tests.helpers.spark_support import find_java_home


@pytest.fixture(scope="session")
def spark_session(tmp_path_factory):
    pytest.importorskip("pyspark", reason="the SQL tests need PySpark: run `uv sync --group sql`")
    java_home = find_java_home()
    if java_home is None:
        pytest.skip(
            "the SQL tests need Java 17 to 21 (none found). Install a JDK, or set JAVA_HOME to one."
        )
    os.environ["JAVA_HOME"] = java_home
    # The pipeline fixes its time zone to UTC (ADR-015). Python converts timestamps with the
    # machine's zone, so pin that too, or a test run in London would show every time an hour out.
    os.environ["TZ"] = "UTC"
    time.tzset()

    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[2]")
        .appName("payments-lakehouse-sql-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        # The fixtures are a few rows. Adaptive execution and big thread pools only add threads
        # (the reconciliation's 25 subqueries once exhausted the machine's thread limit).
        .config("spark.sql.adaptive.enabled", "false")
        .config("spark.sql.exchange.maxThreadThreshold", "4")
        .config("spark.sql.subquery.maxThreadThreshold", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("warehouse")))
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture
def spark(spark_session):
    # A new session shares the JVM but has its own temp views, so no test sees another's tables.
    return spark_session.newSession()
