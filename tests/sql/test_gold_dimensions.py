"""The gold dimensions: the calendar, the transaction types, and the customer versions.

dim_date is where a wrong day or a missing holiday would quietly corrupt every KPI, so it is checked
against known dates. dim_customer's history is built by Auto CDC, which only runs on Databricks, so
only the reporting view over that history is tested here (keys, open end, Unknown member).
"""

from datetime import date, datetime

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import BANK_HOLIDAYS, CUSTOMER_HISTORY, holiday

pytestmark = pytest.mark.sql


def calendar(spark, holidays=()):
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, list(holidays))
    return run_query(spark, "gold/dim_date.sql", "workspace.gold.dim_date")


def by_date(rows):
    return {r["calendar_date"]: r for r in rows}


# ---- dim_date -----------------------------------------------------------------------------------


def test_the_calendar_has_one_row_for_every_day_from_2026_to_2028(spark):
    rows = calendar(spark)
    days = [r["calendar_date"] for r in rows]
    assert len(rows) == 1096 == (date(2028, 12, 31) - date(2026, 1, 1)).days + 1
    assert min(days) == date(2026, 1, 1) and max(days) == date(2028, 12, 31)
    assert len(set(days)) == len(days)
    assert len({r["date_key"] for r in rows}) == 1096


def test_the_leap_day_exists_in_2028_and_not_in_the_other_years(spark):
    days = by_date(calendar(spark))
    assert date(2028, 2, 29) in days
    assert {d for d in days if d.month == 2 and d.day == 29} == {date(2028, 2, 29)}


def test_the_date_key_is_the_year_month_day_integer(spark):
    row = by_date(calendar(spark))[date(2026, 10, 6)]
    assert row["date_key"] == 20261006
    assert by_date(calendar(spark))[date(2028, 12, 31)]["date_key"] == 20281231


def test_calendar_attributes_are_right_for_known_days(spark):
    days = by_date(calendar(spark))
    tuesday = days[date(2026, 10, 6)]
    assert (tuesday["day_name"], tuesday["month_name"], tuesday["calendar_year"]) == (
        "Tuesday", "October", 2026,
    )  # fmt: skip
    assert (tuesday["calendar_month"], tuesday["day_of_month"]) == (10, 6)
    assert tuesday["iso_week"] == date(2026, 10, 6).isocalendar()[1]
    assert tuesday["is_weekend"] is False


@pytest.mark.parametrize(
    ("day", "weekend"),
    [
        (date(2026, 10, 9), False),  # Friday
        (date(2026, 10, 10), True),  # Saturday
        (date(2026, 10, 11), True),  # Sunday
        (date(2026, 10, 12), False),  # Monday
    ],
)
def test_the_weekend_is_saturday_and_sunday(spark, day, weekend):
    assert by_date(calendar(spark))[day]["is_weekend"] is weekend


def test_each_region_has_its_own_holiday_flag(spark):
    rows = by_date(
        calendar(
            spark,
            [
                holiday("2026-12-25", "england-and-wales", "Christmas Day"),
                holiday("2026-12-25", "scotland", "Christmas Day"),
                holiday("2026-12-25", "northern-ireland", "Christmas Day"),
                holiday("2026-08-31", "england-and-wales", "Summer bank holiday"),
                holiday("2026-01-02", "scotland", "2nd January"),
                holiday("2026-03-17", "northern-ireland", "St Patrick's Day"),
            ],
        )
    )
    flags = {
        d: (r["is_bank_holiday_eaw"], r["is_bank_holiday_scotland"], r["is_bank_holiday_ni"])
        for d, r in rows.items()
    }
    assert flags[date(2026, 12, 25)] == (True, True, True)
    assert flags[date(2026, 8, 31)] == (True, False, False)
    assert flags[date(2026, 1, 2)] == (False, True, False)
    assert flags[date(2026, 3, 17)] == (False, False, True)
    assert flags[date(2026, 6, 15)] == (False, False, False)
    assert rows[date(2026, 12, 25)]["bank_holiday_name_eaw"] == "Christmas Day"
    assert rows[date(2026, 1, 2)]["bank_holiday_name_eaw"] is None


def test_two_holiday_names_on_one_day_still_give_one_calendar_row(spark):
    rows = calendar(
        spark,
        [holiday("2026-12-28", title="Boxing Day"), holiday("2026-12-28", title="Substitute day")],
    )
    assert len(rows) == 1096
    assert by_date(rows)[date(2026, 12, 28)]["is_bank_holiday_eaw"] is True


def test_a_holiday_outside_the_calendar_adds_no_row(spark):
    assert len(calendar(spark, [holiday("2025-12-25"), holiday("2029-01-01")])) == 1096


def test_the_calendar_breaks_none_of_its_expectations(spark):
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, [holiday("2026-12-25")])
    register_query(spark, "gold/dim_date.sql", "workspace.gold.dim_date", "calendar")
    for expectation in find(load("gold/dim_date.sql"), "workspace.gold.dim_date").expectations:
        assert violations(spark, "calendar", expectation) == [], expectation.name


# ---- dim_type -----------------------------------------------------------------------------------


def types(spark):
    return run_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type")


def test_there_are_five_types_with_unique_keys_and_names(spark):
    rows = types(spark)
    assert {r["type_name"] for r in rows} == {"CASH_IN", "CASH_OUT", "PAYMENT", "TRANSFER", "DEBIT"}
    assert sorted(r["type_key"] for r in rows) == [1, 2, 3, 4, 5]


def test_only_cash_out_and_transfer_can_be_fraud(spark):
    # Observed in PaySim (docs/data_profile.md); the generator injects fraud on the same two.
    assert {r["type_name"] for r in types(spark) if r["can_be_fraud"]} == {"CASH_OUT", "TRANSFER"}


def test_the_type_dimension_breaks_none_of_its_expectations(spark):
    register_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type", "type_dim")
    for expectation in find(load("gold/dim_type.sql"), "workspace.gold.dim_type").expectations:
        assert violations(spark, "type_dim", expectation) == [], expectation.name


# ---- dim_customer (the reporting view over the Auto CDC history) ---------------------------------


def customers(spark, history):
    make_table(spark, "customer_history", CUSTOMER_HISTORY, history)
    return run_query(spark, "gold/dim_customer.sql", "workspace.gold.dim_customer")


def history_row(customer_id, start, end=None, segment="standard", region="London", kind="customer"):
    return {
        "customer_id": customer_id,
        "entity_type": kind,
        "segment": segment,
        "region": region,
        "changed_at": start,
        "__start_at": start,
        "__end_at": end,
    }


def test_the_open_end_of_the_current_version_becomes_the_far_future_date(spark):
    rows = customers(
        spark,
        [
            history_row("CG1", "2026-09-20 00:00:00", "2026-09-25 12:00:00", "standard"),
            history_row("CG1", "2026-09-25 12:00:00", None, "premium"),
        ],
    )
    versions = sorted((r for r in rows if r["customer_id"] == "CG1"), key=lambda r: r["valid_from"])
    assert [r["valid_to"] for r in versions] == [
        datetime(2026, 9, 25, 12, 0),
        datetime(9999, 12, 31),
    ]
    assert [r["is_current"] for r in versions] == [False, True]
    assert [r["segment"] for r in versions] == ["standard", "premium"]


def test_every_version_gets_its_own_key_and_the_key_is_stable_between_builds(spark):
    history = [
        history_row("CG1", "2026-09-20 00:00:00", "2026-09-25 12:00:00"),
        history_row("CG1", "2026-09-25 12:00:00", None, "premium"),
        history_row("CG2", "2026-09-20 00:00:00", None),
    ]
    first = [r for r in customers(spark, history) if r["customer_key"] != -1]
    second = [r for r in customers(spark, history) if r["customer_key"] != -1]
    assert len({r["customer_key"] for r in first}) == 3
    assert sorted(r["customer_key"] for r in first) == sorted(r["customer_key"] for r in second)


def test_the_unknown_member_is_always_there_exactly_once(spark):
    rows = customers(spark, [history_row("CG1", "2026-09-20 00:00:00")])
    (unknown,) = [r for r in rows if r["customer_key"] == -1]
    assert unknown["customer_id"] == "UNKNOWN" and unknown["entity_type"] == "unknown"
    assert unknown["valid_from"] == datetime(1970, 1, 1) and unknown["valid_to"] == datetime(
        9999, 12, 31
    )
    assert len(rows) == 2


def test_the_customer_dimension_breaks_none_of_its_expectations(spark):
    make_table(
        spark,
        "customer_history",
        CUSTOMER_HISTORY,
        [
            history_row("CG1", "2026-09-20 00:00:00", "2026-09-25 12:00:00"),
            history_row("CG1", "2026-09-25 12:00:00"),
            history_row("MG1", "2026-09-20 00:00:00", kind="merchant"),
        ],
    )
    register_query(spark, "gold/dim_customer.sql", "workspace.gold.dim_customer", "customer_dim")
    statement = find(load("gold/dim_customer.sql"), "workspace.gold.dim_customer")
    assert {e.name for e in statement.expectations} >= {"valid_range", "known_entity_type"}
    for expectation in statement.expectations:
        assert violations(spark, "customer_dim", expectation) == [], expectation.name
