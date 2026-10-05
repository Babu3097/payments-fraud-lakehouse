"""The silver rules for the generated daily feed (flow unify_generated).

Each defect class the feed can carry must produce exactly its reason code, because the silver
reconciliation compares the quarantine counts with the generator's manifests code by code. These
tests pin the rules down one case at a time, including the boundaries that production data never
reaches.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from tests.helpers.spark_support import make_table, run_query
from tests.sql.fixtures import BANK_HOLIDAYS, TRANSACTIONS_DAILY, holiday, txn

pytestmark = pytest.mark.sql

FILE = "silver/transactions_unified.sql"


def unify(spark, *rows, holidays=()):
    make_table(spark, "workspace.bronze.transactions_daily", TRANSACTIONS_DAILY, list(rows))
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, list(holidays))
    return run_query(spark, FILE, "unify_generated")


def only(rows):
    assert len(rows) == 1, rows
    return rows[0]


def test_a_clean_row_passes_every_check_and_is_mapped_field_by_field(spark):
    row = only(unify(spark, txn()))
    assert row["failed_checks"] == []
    assert row["event_id"] == "T20261006000001"
    assert row["event_ts"] == datetime(2026, 10, 6, 10, 30)
    assert row["event_date"] == date(2026, 10, 6)
    assert row["txn_type"] == "PAYMENT"
    assert row["amount"] == Decimal("25.50")
    assert row["counterparty_kind"] == "merchant"
    assert row["is_fraud"] is False
    assert row["is_zero_amount"] is False
    assert row["is_bank_holiday"] is False
    assert row["status_source"] == "source_system"
    assert row["channel"] == "app"
    assert row["event_ts_raw"] == "2026-10-06T10:30:00Z"
    # The generator has no recipient balances, so they stay unknown (not zero).
    assert row["dest_balance_before"] is None and row["dest_balance_after"] is None


@pytest.mark.parametrize(
    "field", ["customer_id", "amount", "type", "event_ts", "event_id", "status"]
)
def test_a_missing_required_field_is_one_reason_and_nothing_else(spark, field):
    row = only(unify(spark, txn(**{field: None})))
    assert row["failed_checks"] == ["NULL_REQUIRED_FIELD"]


def test_a_negative_amount_is_quarantined_but_zero_is_allowed(spark):
    negative, zero = unify(spark, txn(event_id="N", amount="-0.01"), txn(event_id="Z", amount="0"))
    by_id = {r["event_id"]: r for r in (negative, zero)}
    assert by_id["N"]["failed_checks"] == ["NEGATIVE_AMOUNT"]
    assert by_id["Z"]["failed_checks"] == []
    assert by_id["Z"]["is_zero_amount"] is True


@pytest.mark.parametrize("bad_type", ["REFUND", "payment", "PAYMENT ", "", "TRANSFER2"])
def test_the_type_check_is_an_exact_match(spark, bad_type):
    # Strict on purpose: a type the source invents, or writes in another case, is a contract
    # change a person should look at, not something to guess at.
    row = only(unify(spark, txn(type=bad_type)))
    assert row["failed_checks"] == ["UNKNOWN_TYPE"]


@pytest.mark.parametrize("good_type", ["CASH_OUT", "PAYMENT", "CASH_IN", "TRANSFER", "DEBIT"])
def test_each_of_the_five_known_types_passes(spark, good_type):
    assert only(unify(spark, txn(type=good_type)))["failed_checks"] == []


@pytest.mark.parametrize("bad_status", ["PENDING", "approved", "APPROVED ", "", "DECLINED2"])
def test_an_unknown_status_is_quarantined_and_the_check_is_an_exact_match(spark, bad_status):
    assert only(unify(spark, txn(status=bad_status)))["failed_checks"] == ["UNKNOWN_STATUS"]


@pytest.mark.parametrize("good_status", ["APPROVED", "DECLINED"])
def test_each_of_the_two_known_statuses_passes(spark, good_status):
    assert only(unify(spark, txn(status=good_status)))["failed_checks"] == []


def test_a_timestamp_that_cannot_be_read_is_quarantined_and_the_original_text_is_kept(spark):
    row = only(unify(spark, txn(event_ts="not-a-timestamp")))
    assert row["failed_checks"] == ["BAD_TIMESTAMP"]
    assert row["event_ts"] is None and row["event_date"] is None
    assert row["event_ts_raw"] == "not-a-timestamp"


def test_several_defects_give_several_reasons_in_a_fixed_order(spark):
    row = only(unify(spark, txn(amount="-5", type="REFUND", event_ts="garbage")))
    assert row["failed_checks"] == ["NEGATIVE_AMOUNT", "UNKNOWN_TYPE", "BAD_TIMESTAMP"]
    every = only(unify(spark, txn(amount="-5", type="REFUND", event_ts="garbage", status="X")))
    assert every["failed_checks"] == [
        "NEGATIVE_AMOUNT", "UNKNOWN_TYPE", "BAD_TIMESTAMP", "UNKNOWN_STATUS",
    ]  # fmt: skip


def test_a_missing_type_is_not_also_an_unknown_type(spark):
    row = only(unify(spark, txn(type=None, amount="-5")))
    assert row["failed_checks"] == ["NULL_REQUIRED_FIELD", "NEGATIVE_AMOUNT"]


def test_a_missing_counterparty_is_not_a_quarantine_reason(spark):
    # ADR-014: only the hard rules quarantine. A missing counterparty is measured by a soft
    # expectation on silver.transactions, so the row stays in the clean table.
    row = only(unify(spark, txn(counterparty_id=None)))
    assert row["failed_checks"] == []
    assert row["counterparty_kind"] is None


@pytest.mark.parametrize(
    ("counterparty", "kind"),
    [
        ("MG000001", "merchant"),
        ("M1979787155", "merchant"),
        ("CG0000002", "customer"),
        ("X9", None),
    ],
)
def test_the_counterparty_kind_follows_the_first_letter_of_its_id(spark, counterparty, kind):
    assert only(unify(spark, txn(counterparty_id=counterparty)))["counterparty_kind"] == kind


@pytest.mark.parametrize(("raw", "expected"), [(1, True), (0, False), (None, None)])
def test_is_fraud_is_a_boolean_from_the_zero_one_flag(spark, raw, expected):
    assert only(unify(spark, txn(is_fraud=raw)))["is_fraud"] is expected


def test_the_day_is_the_utc_day_of_the_event(spark):
    last, first = unify(
        spark,
        txn(event_id="A", event_ts="2026-10-06T23:59:59Z"),
        txn(event_id="B", event_ts="2026-10-07T00:00:00Z"),
    )
    by_id = {r["event_id"]: r["event_date"] for r in (last, first)}
    assert by_id == {"A": date(2026, 10, 6), "B": date(2026, 10, 7)}


def test_the_bank_holiday_flag_uses_england_and_wales_only(spark):
    holidays = [
        holiday("2026-12-25", "england-and-wales", "Christmas Day"),
        holiday("2026-12-25", "scotland", "Christmas Day"),
        holiday("2026-01-02", "scotland", "2nd January"),
    ]
    rows = unify(
        spark,
        txn(event_id="XMAS", event_ts="2026-12-25T09:00:00Z"),
        txn(event_id="SCOT", event_ts="2026-01-02T09:00:00Z"),
        txn(event_id="PLAIN", event_ts="2026-03-03T09:00:00Z"),
        holidays=holidays,
    )
    flags = {r["event_id"]: r["is_bank_holiday"] for r in rows}
    assert flags == {"XMAS": True, "SCOT": False, "PLAIN": False}


def test_a_date_with_two_holiday_names_never_duplicates_a_transaction(spark):
    holidays = [
        holiday("2026-12-28", title="Boxing Day (substitute)"),
        holiday("2026-12-28", title="A second name for the same day"),
    ]
    rows = unify(spark, txn(event_ts="2026-12-28T09:00:00Z"), holidays=holidays)
    assert len(rows) == 1 and rows[0]["is_bank_holiday"] is True


def test_no_row_is_ever_dropped_by_the_unify_step(spark):
    rows = unify(
        spark,
        txn(event_id="1"),
        txn(event_id="2", amount="-1"),
        txn(event_id="3", type="REFUND"),
        txn(event_id="4", event_ts="garbage"),
        txn(event_id="5", customer_id=None),
    )
    assert sorted(r["event_id"] for r in rows) == ["1", "2", "3", "4", "5"]


# FINDING (Phase 6, fixed): the rule used to check only that Spark could read the text, and Spark
# reads far more than the documented YYYY-MM-DDTHH:MM:SSZ. A time with no date even got the date the
# pipeline runs on, so replaying the same file on another day changed its result. Now only the
# documented format is a timestamp, and each of these is quarantined and left unparsed.
LENIENT_TIMESTAMPS = [
    "2026-10-06",
    "2026-10-06 10:30:00",
    "2026-10-06T10:30:00",
    "2026-10-06T10:30:00+01:00",
    "2026-10-06T10:30:00.123Z",
    " 2026-10-06T10:30:00Z",
    "2026-10",
    "2026",
    "10:30:00",
    "T10:30:00Z",
    "2026-1-6T1:2:3Z",
]


@pytest.mark.parametrize("raw", LENIENT_TIMESTAMPS)
def test_only_the_documented_timestamp_format_is_accepted(spark, raw):
    row = only(unify(spark, txn(event_ts=raw)))
    assert row["failed_checks"] == ["BAD_TIMESTAMP"]
    assert row["event_ts"] is None and row["event_date"] is None  # never guessed at
    assert row["event_ts_raw"] == raw


def test_the_documented_format_is_still_read_exactly(spark):
    row = only(unify(spark, txn(event_ts="2026-10-06T23:59:59Z")))
    assert row["failed_checks"] == [] and row["event_ts"] == datetime(2026, 10, 6, 23, 59, 59)


@pytest.mark.parametrize(
    "raw", ["2026-13-45T10:30:00Z", "2026-02-30T10:30:00Z", "2026-10-06T25:00:00Z"]
)
def test_a_well_shaped_but_impossible_date_or_time_is_quarantined(spark, raw):
    assert only(unify(spark, txn(event_ts=raw)))["failed_checks"] == ["BAD_TIMESTAMP"]
