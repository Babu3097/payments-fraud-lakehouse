"""The silver rules for the PaySim history (flow unify_paysim).

PaySim has no id, no timestamp and no status, so silver derives them (ADR-005, ADR-008). Those
derivations are what every later layer stands on, so they are pinned down here: the time from the
step, the id from a hash of every column, and the status from the source's own flag.
"""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest

from tests.helpers.spark_support import make_table, run_query
from tests.sql.fixtures import BANK_HOLIDAYS, PAYSIM, holiday, paysim

pytestmark = pytest.mark.sql

FILE = "silver/transactions_unified.sql"


def unify(spark, *rows, holidays=()):
    make_table(spark, "workspace.bronze.paysim_transactions", PAYSIM, list(rows))
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, list(holidays))
    return run_query(spark, FILE, "unify_paysim")


def only(rows):
    assert len(rows) == 1, rows
    return rows[0]


def test_the_first_real_paysim_row_is_mapped_field_by_field(spark):
    row = only(unify(spark, paysim()))
    assert row["failed_checks"] == []
    assert row["customer_id"] == "C1231006815" and row["counterparty_id"] == "M1979787155"
    assert row["txn_type"] == "PAYMENT" and row["amount"] == Decimal("9839.64")
    assert row["origin_balance_before"] == Decimal("170136.00")
    assert row["origin_balance_after"] == Decimal("160296.36")
    assert row["source_system"] == "paysim" and row["status_source"] == "derived_paysim_rule"
    # PaySim carries neither a channel nor a raw timestamp text.
    assert row["channel"] is None and row["event_ts_raw"] is None


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (1, datetime(2026, 8, 20, 0, 0)),  # the anchor (ADR-008): step 1 is the first hour
        (2, datetime(2026, 8, 20, 1, 0)),
        (25, datetime(2026, 8, 21, 0, 0)),
        (743, datetime(2026, 9, 19, 22, 0)),  # the last step in the file
    ],
)
def test_the_event_time_is_the_anchor_plus_one_hour_per_step(spark, step, expected):
    row = only(unify(spark, paysim(step=step)))
    assert row["event_ts"] == expected
    assert row["event_date"] == expected.date()


def test_the_event_id_is_a_p_and_24_hex_characters(spark):
    assert re.fullmatch(r"P[0-9a-f]{24}", only(unify(spark, paysim()))["event_id"])


def test_the_same_source_row_always_gets_the_same_id_whatever_the_audit_columns_say(spark):
    # A replay or a second load of the file must produce the same ids, or deduplication fails.
    first = only(unify(spark, paysim(_source_file="a.csv", _ingested_at="2026-10-04 09:01:00")))
    second = only(unify(spark, paysim(_source_file="b.csv", _ingested_at="2027-01-01 00:00:00")))
    assert first["event_id"] == second["event_id"]


HASHED_COLUMNS = {
    "step": 2,
    "type": "CASH_OUT",
    "amount": "1.00",
    "nameOrig": "C0000000001",
    "oldbalanceOrg": "5.00",
    "newbalanceOrig": "6.00",
    "nameDest": "C0000000002",
    "oldbalanceDest": "7.00",
    "newbalanceDest": "8.00",
    "isFraud": 1,
    "isFlaggedFraud": 1,
}


@pytest.mark.parametrize(("column", "other"), HASHED_COLUMNS.items())
def test_changing_any_one_source_column_changes_the_id(spark, column, other):
    # The id is a hash of every column because the file has no key. If a column dropped out of the
    # hash, two different transactions could share an id and deduplication would delete one.
    base = only(unify(spark, paysim()))["event_id"]
    changed = only(unify(spark, paysim(**{column: other})))["event_id"]
    assert changed != base


def test_a_flagged_row_is_declined_with_the_source_reason_and_any_other_is_approved(spark):
    flagged, plain = unify(spark, paysim(isFlaggedFraud=1), paysim(step=2, isFlaggedFraud=0))
    by_status = {r["status"]: r for r in (flagged, plain)}
    assert by_status["DECLINED"]["decline_reason"] == "PAYSIM_FLAGGED"
    assert by_status["APPROVED"]["decline_reason"] is None


def test_a_merchant_recipient_has_unknown_balances_but_a_customer_keeps_them(spark):
    # In the file a merchant's balances are 0, which means unknown, so they become NULL.
    merchant, customer = unify(
        spark,
        paysim(nameDest="M123", oldbalanceDest="0.00", newbalanceDest="0.00"),
        paysim(step=2, nameDest="C456", oldbalanceDest="50.00", newbalanceDest="60.00"),
    )
    by_kind = {r["counterparty_kind"]: r for r in (merchant, customer)}
    assert by_kind["merchant"]["dest_balance_before"] is None
    assert by_kind["merchant"]["dest_balance_after"] is None
    assert by_kind["customer"]["dest_balance_before"] == Decimal("50.00")
    assert by_kind["customer"]["dest_balance_after"] == Decimal("60.00")


@pytest.mark.parametrize(("raw", "expected"), [(1, True), (0, False)])
def test_is_fraud_follows_the_source_label(spark, raw, expected):
    assert only(unify(spark, paysim(isFraud=raw)))["is_fraud"] is expected


def test_a_zero_amount_is_flagged_but_not_quarantined(spark):
    row = only(unify(spark, paysim(amount="0.00")))
    assert row["is_zero_amount"] is True and row["failed_checks"] == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"amount": "-1.00"}, ["NEGATIVE_AMOUNT"]),
        ({"type": "REFUND"}, ["UNKNOWN_TYPE"]),
        ({"type": None}, ["NULL_REQUIRED_FIELD"]),
        ({"amount": None}, ["NULL_REQUIRED_FIELD"]),
        ({"nameOrig": None}, ["NULL_REQUIRED_FIELD"]),
        ({"step": None}, ["NULL_REQUIRED_FIELD"]),
        ({"amount": "-1.00", "type": "REFUND"}, ["NEGATIVE_AMOUNT", "UNKNOWN_TYPE"]),
    ],
    ids=lambda v: str(v),
)
def test_each_defect_gives_exactly_its_reasons(spark, overrides, expected):
    assert only(unify(spark, paysim(**overrides)))["failed_checks"] == expected


def test_a_missing_step_leaves_no_event_time_and_still_keeps_the_row(spark):
    row = only(unify(spark, paysim(step=None)))
    assert row["event_ts"] is None and row["event_date"] is None


def test_the_bank_holiday_flag_comes_from_the_date_the_step_works_out_to(spark):
    # Step 265 is 2026-08-31, the summer bank holiday in England and Wales; step 1 is not a holiday.
    rows = unify(
        spark,
        paysim(step=265),
        paysim(step=1, nameOrig="C2"),
        holidays=[holiday("2026-08-31", title="Summer bank holiday")],
    )
    by_date = {r["event_date"]: r["is_bank_holiday"] for r in rows}
    assert by_date == {date(2026, 8, 31): True, date(2026, 8, 20): False}
