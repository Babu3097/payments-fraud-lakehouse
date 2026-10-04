"""The silver split into clean and quarantined rows, and the expectations that back it up.

The split sends a row to `silver.transactions` or to `silver.transactions_quarantine` depending on
its failed_checks. The expectations on the clean table are a safety net (ADR-014): if the split ever
let a bad row through, a hard rule should stop the update. These tests check both halves, and that
the net has no hole for any defect the split is meant to catch.
"""

import json

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import register_query, run_query, violations
from tests.sql.fixtures import holiday, paysim, txn
from tests.sql.steps import build_unified

pytestmark = pytest.mark.sql

CLEAN_TABLE = find(load("silver/transactions.sql"), "workspace.silver.transactions")
EXPECTATIONS = {e.name: e for e in CLEAN_TABLE.expectations}
HARD = [e for e in CLEAN_TABLE.expectations if e.on_violation == "FAIL UPDATE"]

# One row per defect class the split is meant to catch, for each source.
DEFECTIVE_GENERATED = {
    "missing customer": txn(event_id="G-CUST", customer_id=None),
    "missing amount": txn(event_id="G-AMT", amount=None),
    "missing type": txn(event_id="G-TYPE", type=None),
    "missing time": txn(event_id="G-TS", event_ts=None),
    "negative amount": txn(event_id="G-NEG", amount="-1.00"),
    "unknown type": txn(event_id="G-UNK", type="REFUND"),
    "bad timestamp": txn(event_id="G-BAD", event_ts="not-a-timestamp"),
    "two defects": txn(event_id="G-TWO", amount="-1.00", type="REFUND"),
    "unknown status": txn(event_id="G-STAT", status="PENDING"),
    "missing status": txn(event_id="G-NOSTAT", status=None),
    "missing event id": txn(event_id=None),
}
CLEAN_GENERATED = [
    txn(event_id="G-OK1"),
    txn(event_id="G-OK2", status="DECLINED", decline_reason="INSUFFICIENT_FUNDS"),
]


def split(spark, generated=(), paysim_rows=()):
    build_unified(spark, generated, paysim_rows, [holiday("2026-12-25")])
    clean = run_query(spark, "silver/transactions.sql", "transactions_valid")
    quarantined = run_query(spark, "silver/transactions_quarantine.sql", "transactions_quarantine")
    return clean, quarantined


def test_every_row_goes_to_exactly_one_side_and_none_is_lost(spark):
    generated = [*CLEAN_GENERATED, *DEFECTIVE_GENERATED.values()]
    paysim_rows = [paysim(), paysim(step=2, amount="-5.00")]
    unified = build_unified(spark, generated, paysim_rows, []).collect()
    clean, quarantined = split(spark, generated, paysim_rows)
    clean_ids = {r["event_id"] for r in clean}
    quarantined_ids = {r["event_id"] for r in quarantined}
    assert clean_ids | quarantined_ids == {r["event_id"] for r in unified}
    assert clean_ids & quarantined_ids == set()
    assert len(clean) + len(quarantined) == len(unified) == len(generated) + len(paysim_rows)


def test_the_clean_side_has_no_reasons_and_the_quarantine_side_has_at_least_one(spark):
    clean, quarantined = split(
        spark,
        [*CLEAN_GENERATED, *DEFECTIVE_GENERATED.values()],
        [paysim(), paysim(step=2, type="X")],
    )
    assert all(r["failed_checks"] == [] for r in clean)
    assert all(len(r["failed_checks"]) >= 1 for r in quarantined)
    assert {r["event_id"] for r in quarantined} >= {
        r["event_id"] for r in DEFECTIVE_GENERATED.values()
    }


def test_a_quarantined_row_keeps_its_original_values_with_nulls_written_out(spark):
    _, quarantined = split(
        spark,
        [
            DEFECTIVE_GENERATED["missing customer"],
            DEFECTIVE_GENERATED["bad timestamp"],
        ],
    )
    records = {r["event_id"]: json.loads(r["raw_record"]) for r in quarantined}
    missing = records["G-CUST"]
    # A steward must be able to see WHICH field was missing, so the key is there and its value null.
    assert "customer_id" in missing and missing["customer_id"] is None
    assert records["G-BAD"]["event_ts_raw"] == "not-a-timestamp"
    assert set(missing) == {
        "event_id", "event_ts_raw", "txn_type", "amount", "customer_id", "counterparty_id",
        "origin_balance_before", "origin_balance_after", "status", "decline_reason", "is_fraud",
        "channel",
    }  # fmt: skip


def test_a_quarantined_row_carries_its_reasons_source_and_a_quarantine_time(spark):
    _, quarantined = split(spark, [DEFECTIVE_GENERATED["two defects"]])
    (row,) = quarantined
    assert row["failed_checks"] == ["NEGATIVE_AMOUNT", "UNKNOWN_TYPE"]
    assert row["source_system"] == "generator-v1"
    assert row["_source_file"] == "transactions_2026-10-06.jsonl"
    assert row["quarantined_at"] is not None


def test_the_quarantine_table_expectations_hold_for_every_quarantined_row(spark):
    rows = [r for label, r in DEFECTIVE_GENERATED.items() if label != "missing event id"]
    _, quarantined = split(spark, rows, [paysim(step=2, amount="-5")])
    assert quarantined
    statement = find(
        load("silver/transactions_quarantine.sql"), "workspace.silver.transactions_quarantine"
    )
    register_query(
        spark, "silver/transactions_quarantine.sql", "transactions_quarantine", "quarantine_rows"
    )
    for expectation in statement.expectations:
        assert violations(spark, "quarantine_rows", expectation) == [], expectation.name


def test_clean_rows_from_both_sources_break_no_expectation_at_all(spark):
    build_unified(spark, CLEAN_GENERATED, [paysim(), paysim(step=743, isFlaggedFraud=1)], [])
    register_query(spark, "silver/transactions.sql", "transactions_valid", "clean_rows")
    assert spark.table("clean_rows").count() == 4
    for expectation in CLEAN_TABLE.expectations:
        assert violations(spark, "clean_rows", expectation) == [], expectation.name


@pytest.mark.parametrize("label", sorted(DEFECTIVE_GENERATED))
def test_the_safety_net_has_no_hole_for_any_defect_the_split_catches(spark, label):
    # Force the defective row into the clean table by skipping the split, as if the split had a
    # bug. At least one HARD expectation must refuse it, or a bad row could reach gold.
    build_unified(spark, [DEFECTIVE_GENERATED[label]], [], []).createOrReplaceTempView(
        "forced_rows"
    )
    broken = [e.name for e in HARD if violations(spark, "forced_rows", e)]
    assert broken, f"{label!r} would pass every hard expectation"


def test_the_hard_and_soft_expectations_are_the_ones_the_design_says(spark):
    # ADR-014: the structural rules fail the update, the descriptive rules only measure.
    assert {e.name for e in HARD} == {
        "event_id_present", "event_ts_present", "customer_present",
        "amount_not_negative", "known_type", "known_status",
    }  # fmt: skip
    soft = {e.name for e in CLEAN_TABLE.expectations if e.on_violation == "WARN"}
    assert soft == {"decline_has_reason", "counterparty_present", "event_date_in_range"}


# FINDING (Phase 6, fixed): an unknown STATUS or a missing event_id used to pass the split and trip
# a HARD expectation, which stopped the whole daily update instead of setting one row aside, unlike
# an unknown TYPE. They are quarantined now; the hard expectations remain only as the safety net.
def test_an_unknown_status_or_a_missing_id_is_quarantined_not_left_to_fail_the_update(spark):
    _, quarantined = split(
        spark,
        [txn(event_id="STAT", status="PENDING"), txn(event_id=None), txn(event_id="OK")],
    )
    reasons = sorted(tuple(r["failed_checks"]) for r in quarantined)
    assert reasons == [("NULL_REQUIRED_FIELD",), ("UNKNOWN_STATUS",)]


def test_a_quarantined_row_with_no_id_is_the_one_case_the_soft_has_event_id_check_measures(spark):
    # The row is kept with its reason and original values (nothing is dropped), but it has no key,
    # so the soft expectation on the quarantine table counts it. That count is the visible signal.
    build_unified(spark, [txn(event_id=None)], [], [])
    register_query(spark, "silver/transactions_quarantine.sql", "transactions_quarantine", "q")
    statement = find(
        load("silver/transactions_quarantine.sql"), "workspace.silver.transactions_quarantine"
    )
    broken = {e.name for e in statement.expectations if violations(spark, "q", e)}
    assert broken == {"has_event_id"}
    assert {e.name: e.on_violation for e in statement.expectations}["has_event_id"] == "WARN"


def test_a_date_past_the_calendar_is_not_quarantined_it_only_warns(spark):
    # The calendar ends on 2028-12-31 (ADR-024). Beyond it a row is kept, with a warning, and gold's
    # reconciliation is what fails, so the calendar running out cannot pass silently.
    unified = build_unified(spark, [txn(event_ts="2029-01-01T00:00:00Z")], [], [])
    assert unified.collect()[0]["failed_checks"] == []
    unified.createOrReplaceTempView("rows")
    assert [v["event_id"] for v in violations(spark, "rows", EXPECTATIONS["event_date_in_range"])]
    assert [e.name for e in HARD if violations(spark, "rows", e)] == []
