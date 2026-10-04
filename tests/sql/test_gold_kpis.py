"""The gold KPI views. Each is checked against numbers worked out by hand on a few rows, so a wrong
numerator, denominator or grouping shows up as a different number, not as a plausible-looking one.

The fact rows are built directly (not through the fact SQL), so these tests fail only when a KPI
view is wrong, not when the fact is.
"""

from decimal import Decimal

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import BANK_HOLIDAYS, FACT, fact, holiday

pytestmark = pytest.mark.sql


def kpis(spark, view_file, name, fact_rows, holidays=()):
    make_table(spark, "workspace.gold.fact_transactions", FACT, list(fact_rows))
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, list(holidays))
    register_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type", "gold_dim_type")
    register_query(spark, "gold/dim_date.sql", "workspace.gold.dim_date", "gold_dim_date")
    return run_query(spark, view_file, name)


def daily(spark, fact_rows, holidays=()):
    return kpis(spark, "gold/kpi_daily.sql", "workspace.gold.kpi_daily", fact_rows, holidays)


# One generator day worked out by hand:
#   four rows, three approved and one declined, two of them fraud (one approved = missed, one
#   declined = caught).
DAY = [
    fact(event_id="K1", amount="100.00"),
    fact(event_id="K2", amount="50.50"),
    fact(event_id="K3", amount="200.00", is_fraud=True),
    fact(event_id="K4", amount="300.00", is_fraud=True, is_declined=True, status="DECLINED"),
]


def test_daily_volume_value_and_rates_match_the_hand_worked_numbers(spark):
    (row,) = daily(spark, DAY)
    assert row["date_key"] == 20261006 and row["source_system"] == "generator-v1"
    assert row["txn_count"] == 4 and row["txn_value"] == Decimal("650.50")
    assert row["approved_count"] == 3 and row["approved_value"] == Decimal("350.50")
    assert row["declined_count"] == 1
    assert row["approval_rate"] == pytest.approx(0.75)
    assert row["fraud_count"] == 2 and row["fraud_value"] == Decimal("500.00")
    assert row["fraud_rate"] == pytest.approx(0.5)


def test_missed_fraud_is_fraud_that_was_approved_and_catch_rate_is_fraud_that_was_declined(spark):
    (row,) = daily(spark, DAY)
    assert row["fraud_missed_count"] == 1 and row["fraud_missed_value"] == Decimal("200.00")
    assert row["fraud_catch_rate"] == pytest.approx(0.5)


def test_a_day_with_no_fraud_has_a_zero_fraud_rate_and_no_catch_rate_instead_of_an_error(spark):
    (row,) = daily(spark, [fact(event_id="A"), fact(event_id="B")])
    assert row["fraud_count"] == 0 and row["fraud_rate"] == 0
    assert row["fraud_catch_rate"] is None  # nothing to catch, so the ratio does not exist


def test_the_two_sources_stay_apart_on_the_same_day(spark):
    rows = daily(
        spark,
        [
            fact(event_id="G", source_system="generator-v1"),
            fact(event_id="P1", source_system="paysim"),
            fact(event_id="P2", source_system="paysim"),
        ],
    )
    assert {r["source_system"]: r["txn_count"] for r in rows} == {"generator-v1": 1, "paysim": 2}


def test_each_day_is_its_own_row(spark):
    rows = daily(spark, [fact(event_id="A"), fact(event_id="B", date_key=20261007)])
    assert sorted(r["date_key"] for r in rows) == [20261006, 20261007]


def test_the_holiday_flag_comes_from_the_calendar(spark):
    rows = daily(
        spark,
        [fact(event_id="X", date_key=20261225), fact(event_id="N", date_key=20261224)],
        holidays=[holiday("2026-12-25", title="Christmas Day")],
    )
    assert {r["date_key"]: r["is_bank_holiday_eaw"] for r in rows} == {
        20261225: True,
        20261224: False,
    }


def test_a_fact_row_whose_date_is_not_in_the_calendar_is_left_out_of_the_kpi(spark):
    # The join to dim_date is an inner join, so a date past the calendar would vanish from the KPI
    # without an error. The gold reconciliation (no orphan keys) is what catches that (ADR-024).
    rows = daily(spark, [fact(event_id="IN"), fact(event_id="OUT", date_key=20290101)])
    assert [r["date_key"] for r in rows] == [20261006]


def test_the_rates_are_always_between_zero_and_one(spark):
    make_table(spark, "workspace.gold.fact_transactions", FACT, DAY)
    make_table(spark, "workspace.silver.bank_holidays", BANK_HOLIDAYS, [])
    register_query(spark, "gold/dim_date.sql", "workspace.gold.dim_date", "gold_dim_date")
    register_query(spark, "gold/kpi_daily.sql", "workspace.gold.kpi_daily", "kpi_rows")
    for expectation in find(load("gold/kpi_daily.sql"), "workspace.gold.kpi_daily").expectations:
        assert violations(spark, "kpi_rows", expectation) == [], expectation.name


# ---- fraud by type, amount band and hour --------------------------------------------------------

MIX = [
    fact(event_id="T1", type_key=4, amount="100.00"),
    fact(event_id="T2", type_key=4, amount="300.00", is_fraud=True),
    fact(event_id="T3", type_key=4, amount="200.00"),
    fact(event_id="C1", type_key=2, amount="1000.00", is_fraud=True, amount_band_sort=4,
         amount_band="1,000 to 9,999.99", hour_of_day=2, day_part="night"),
    fact(event_id="P1", type_key=4, amount="50.00", source_system="paysim",
         amount_band_sort=2, amount_band="10 to 99.99"),
]  # fmt: skip


def test_fraud_by_type_counts_each_type_and_source_separately(spark):
    rows = kpis(spark, "gold/kpi_fraud_by_type.sql", "workspace.gold.kpi_fraud_by_type", MIX)
    by_key = {(r["type_name"], r["source_system"]): r for r in rows}
    transfer = by_key[("TRANSFER", "generator-v1")]
    assert transfer["txn_count"] == 3 and transfer["txn_value"] == Decimal("600.00")
    assert transfer["avg_amount"] == Decimal("200")
    assert transfer["fraud_count"] == 1 and transfer["fraud_value"] == Decimal("300.00")
    assert transfer["fraud_rate"] == pytest.approx(1 / 3)
    cash_out = by_key[("CASH_OUT", "generator-v1")]
    assert (cash_out["txn_count"], cash_out["fraud_rate"]) == (1, 1.0)
    assert by_key[("TRANSFER", "paysim")]["fraud_count"] == 0
    assert len(rows) == 3


def test_fraud_by_amount_band_keeps_the_band_order_key(spark):
    rows = kpis(
        spark, "gold/kpi_fraud_by_amount_band.sql", "workspace.gold.kpi_fraud_by_amount_band", MIX
    )
    by_key = {(r["amount_band_sort"], r["source_system"]): r for r in rows}
    assert by_key[(3, "generator-v1")]["txn_count"] == 3
    assert by_key[(3, "generator-v1")]["amount_band"] == "100 to 999.99"
    assert by_key[(4, "generator-v1")]["fraud_rate"] == 1.0
    assert by_key[(2, "paysim")]["txn_count"] == 1


def test_fraud_by_hour_groups_by_hour_day_part_and_source(spark):
    rows = kpis(spark, "gold/kpi_fraud_by_hour.sql", "workspace.gold.kpi_fraud_by_hour", MIX)
    by_key = {(r["hour_of_day"], r["source_system"]): r for r in rows}
    assert by_key[(10, "generator-v1")]["txn_count"] == 3
    assert by_key[(2, "generator-v1")]["day_part"] == "night"
    assert by_key[(2, "generator-v1")]["fraud_count"] == 1
    assert len(rows) == 3
