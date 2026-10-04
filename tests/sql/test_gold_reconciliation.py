"""The gold reconciliation: the gate that fails the pipeline when gold stops matching the source.

Two things are proved. On a consistent small warehouse, built through the real silver and gold SQL,
every constraint passes, so the gate raises no false alarm. Then the warehouse is corrupted in a
different way for each constraint, and the matching constraint must fail, so the gate has no false
pass. A constraint that no test can trip is a constraint nobody has shown to work.
"""

from decimal import Decimal

import pytest
from pyspark.sql import functions as F

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import violations
from tests.sql.steps import build_universe

pytestmark = pytest.mark.sql

CONSTRAINTS = find(load("gold/reconciliation.sql"), "reconciliation").expectations
BY_NAME = {c.name: c for c in CONSTRAINTS}


def failed_constraints(spark, tamper=None) -> set[str]:
    reconciliation = build_universe(spark, tamper)
    reconciliation.createOrReplaceTempView("recon")
    assert reconciliation.count() == 1  # one row of control totals
    return {c.name for c in CONSTRAINTS if violations(spark, "recon", c)}


def where(condition):
    return lambda frame: frame.where(condition)


def edit(column, value, when):
    return lambda frame: frame.withColumn(
        column, F.when(F.expr(when), value).otherwise(F.col(column))
    )


def test_the_constraints_are_the_twelve_the_design_lists_and_eleven_stop_the_update():
    assert len(CONSTRAINTS) == 12
    soft = [c.name for c in CONSTRAINTS if c.on_violation == "WARN"]
    assert soft == ["versions_match_profile_events"]


def test_a_consistent_warehouse_raises_no_alarm(spark):
    assert failed_constraints(spark) == set()


def test_the_control_totals_are_the_numbers_worked_out_by_hand(spark):
    (row,) = [r.asDict() for r in build_universe(spark).collect()]
    # PaySim: three rows, 850.00 in all, one fraud. Generated: 4 distinct events (G1 arrives twice),
    # two of them clean (25.50 + 100.00) and two quarantined (-5.00 and 10.00).
    assert (row["paysim_rows_gold"], row["paysim_rows_bronze"]) == (3, 3)
    assert row["paysim_amount_gold"] == row["paysim_amount_bronze"] == Decimal("850.00")
    assert (row["paysim_fraud_gold"], row["paysim_fraud_bronze"]) == (1, 1)
    assert row["bronze_generated_events"] == 4
    assert (row["gold_generated_rows"], row["quarantined_events"]) == (2, 2)
    assert row["bronze_generated_amount"] == Decimal("130.50")
    assert (row["gold_generated_amount"], row["quarantined_amount"]) == (
        Decimal("125.50"),
        Decimal("5.00"),
    )
    assert (row["silver_rows"], row["fact_rows"], row["fact_distinct_events"]) == (5, 5, 5)
    assert (row["current_versions"], row["distinct_customers"], row["dim_versions"]) == (3, 3, 4)
    assert row["profile_events"] == 4


def two_open_versions(frame):
    template = frame.where("customer_id = 'CG2'")
    first = template.withColumn("customer_id", F.lit("CG9")).withColumn("customer_key", F.lit(998))
    second = template.withColumn("customer_id", F.lit("CG9")).withColumn("customer_key", F.lit(999))
    return frame.unionByName(first).unionByName(second)


def one_version_without_an_event(frame):
    # An old, closed version that no profile event explains. It covers no transaction, so only the
    # version count and the event count disagree.
    stray = (
        frame.where("customer_id = 'CG2'")
        .withColumn("customer_key", F.lit(997))
        .withColumn("valid_from", F.lit("2020-01-01 00:00:00").cast("timestamp"))
        .withColumn("valid_to", F.lit("2020-02-01 00:00:00").cast("timestamp"))
        .withColumn("is_current", F.lit(False))
    )
    return frame.unionByName(stray)


# Each corruption, the constraint it must trip, and (where the damage is local) nothing else.
FACT = "gold_fact_transactions"
CORRUPTIONS = {
    "paysim_rows_match": (
        {FACT: where("NOT (source_system = 'paysim' AND amount = 100.00)")},
        {"paysim_rows_match", "paysim_amount_matches", "fact_equals_silver"},
    ),
    "paysim_amount_matches": (
        {FACT: edit("amount", Decimal("500.01"), "source_system = 'paysim' AND amount = 500.00")},
        {"paysim_amount_matches"},
    ),
    "paysim_fraud_matches": (
        {FACT: edit("is_fraud", False, "source_system = 'paysim' AND amount = 500.00")},
        {"paysim_fraud_matches"},
    ),
    "generated_events_accounted_for": (
        {FACT: where("event_id <> 'G2'")},
        {"generated_events_accounted_for", "generated_amount_accounted_for", "fact_equals_silver"},
    ),
    "generated_amount_accounted_for": (
        {FACT: edit("amount", Decimal("25.51"), "event_id = 'G1'")},
        {"generated_amount_accounted_for"},
    ),
    "fact_equals_silver": (
        {FACT: where("NOT (source_system = 'paysim' AND amount = 250.00)")},
        {"fact_equals_silver", "paysim_rows_match", "paysim_amount_matches"},
    ),
    "fact_event_id_unique": (
        {FACT: lambda f: f.unionByName(f.where("event_id = 'G1'"))},
        {"fact_event_id_unique", "fact_equals_silver", "generated_events_accounted_for",
         "generated_amount_accounted_for"},
    ),
    "no_orphan_keys": (
        {FACT: edit("date_key", 20290101, "event_id = 'G1'")},
        {"no_orphan_keys", "kpi_daily_matches_fact"},
    ),
    "generated_customers_resolved": (
        {FACT: edit("origin_customer_key", -1, "event_id = 'G1'")},
        {"generated_customers_resolved"},
    ),
    "kpi_daily_matches_fact": (
        {"gold_kpi_daily": edit("txn_count", 999, "source_system = 'paysim'")},
        {"kpi_daily_matches_fact"},
    ),
    "one_current_version_per_customer": (
        # A customer with two open-ended versions. It has no transactions, so the fact does not
        # fan out (a duplicate on a customer WITH transactions would also trip four fact checks).
        {"gold_dim_customer": two_open_versions},
        {"one_current_version_per_customer", "versions_match_profile_events"},
    ),
    "versions_match_profile_events": (
        {"gold_dim_customer": one_version_without_an_event},
        {"versions_match_profile_events"},
    ),
}  # fmt: skip


def test_every_constraint_has_a_corruption_that_trips_it():
    assert set(CORRUPTIONS) == set(BY_NAME)


@pytest.mark.parametrize("constraint", sorted(CORRUPTIONS))
def test_each_corruption_trips_its_own_constraint_and_no_unexpected_one(spark, constraint):
    tamper, expected = CORRUPTIONS[constraint]
    failed = failed_constraints(spark, tamper)
    assert constraint in failed, f"{constraint} did not notice its own corruption"
    assert failed == expected
