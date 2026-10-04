"""The gold fact table: the point-in-time customer join, the derived columns and the rule flags.

Most cases are independent, so they are computed in one Spark run (a grid, one customer per case so
the burst window never mixes them) and each test reads its own row. The join and burst cases need
specific neighbours, so they build their own tables.
"""

from decimal import Decimal

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import DIM_CUSTOMER, SILVER_TRANSACTIONS, silver_txn, version

pytestmark = pytest.mark.sql

FACT_FILE = "gold/fact_transactions.sql"
FACT_NAME = "workspace.gold.fact_transactions"


def build_fact(spark, transactions, customers=()):
    make_table(spark, "workspace.silver.transactions", SILVER_TRANSACTIONS, list(transactions))
    make_table(spark, "workspace.gold.dim_customer", DIM_CUSTOMER, list(customers))
    register_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type", "gold_dim_type")
    return run_query(spark, FACT_FILE, FACT_NAME)


def by_id(rows):
    return {r["event_id"]: r for r in rows}


# ---- the grid: independent cases, one Spark run ----

BANDS = [
    ("0.00", 1, "0 to 9.99"), ("9.99", 1, "0 to 9.99"),
    ("10.00", 2, "10 to 99.99"), ("99.99", 2, "10 to 99.99"),
    ("100.00", 3, "100 to 999.99"), ("999.99", 3, "100 to 999.99"),
    ("1000.00", 4, "1,000 to 9,999.99"), ("9999.99", 4, "1,000 to 9,999.99"),
    ("10000.00", 5, "10,000 to 99,999.99"), ("99999.99", 5, "10,000 to 99,999.99"),
    ("100000.00", 6, "100,000 and over"), ("356179278.92", 6, "100,000 and over"),
]  # fmt: skip
DAY_PARTS = [
    (0, "night"), (5, "night"), (6, "morning"), (11, "morning"),
    (12, "afternoon"), (17, "afternoon"), (18, "evening"), (23, "evening"),
]  # fmt: skip
# (type, amount, balance before, hour, expected balance drain, expected night high value)
RULE_CASES = {
    "drain-transfer": ("TRANSFER", "100.00", "100.00", 10, True, False),
    "drain-cash-out": ("CASH_OUT", "100.00", "100.00", 10, True, False),
    "drain-payment": ("PAYMENT", "100.00", "100.00", 10, False, False),
    "drain-partial": ("TRANSFER", "100.00", "150.00", 10, False, False),
    "drain-zero": ("TRANSFER", "0.00", "0.00", 10, False, False),
    "night-1am": ("TRANSFER", "1000.00", "9000.00", 1, False, True),
    "night-4am": ("TRANSFER", "5000.00", "9000.00", 4, False, True),
    "night-midnight": ("TRANSFER", "5000.00", "9000.00", 0, False, False),
    "night-5am": ("TRANSFER", "5000.00", "9000.00", 5, False, False),
    "night-small": ("TRANSFER", "999.99", "9000.00", 2, False, False),
    "night-cash-out": ("CASH_OUT", "5000.00", "9000.00", 2, False, False),
}


def grid_rows():
    rows = []
    for amount, _, _ in BANDS:
        rows.append(silver_txn(event_id=f"BAND-{amount}", customer_id=f"B{amount}", amount=amount))
    for hour, _ in DAY_PARTS:
        rows.append(
            silver_txn(
                event_id=f"HOUR-{hour}",
                customer_id=f"H{hour}",
                event_ts=f"2026-10-06 {hour:02d}:30:00",
            )
        )
    for name, (kind, amount, before, hour, _, _) in RULE_CASES.items():
        rows.append(
            silver_txn(
                event_id=name,
                customer_id=name,
                txn_type=kind,
                amount=amount,
                origin_balance_before=before,
                event_ts=f"2026-10-06 {hour:02d}:30:00",
            )
        )
    rows += [
        silver_txn(event_id="SRC-DECLINED", customer_id="S1", status="DECLINED",
                   status_source="derived_paysim_rule", decline_reason="PAYSIM_FLAGGED"),
        silver_txn(event_id="SRC-APPROVED", customer_id="S2", status_source="derived_paysim_rule"),
        silver_txn(event_id="GEN-DECLINED", customer_id="S3", status="DECLINED",
                   decline_reason="LIMIT"),
        silver_txn(event_id="PASS-THROUGH", customer_id="P1", txn_type="CASH_IN", amount="42.10",
                   channel="web", source_system="paysim", is_fraud=True, counterparty_id="MG9",
                   event_ts="2026-12-31 23:59:59"),
        silver_txn(event_id="UNKNOWN-TYPE", customer_id="U1", txn_type="REFUND"),
    ]  # fmt: skip
    return rows


@pytest.fixture(scope="module")
def grid(spark_session):
    session = spark_session.newSession()
    return by_id(build_fact(session, grid_rows()))


@pytest.mark.parametrize(("amount", "sort", "label"), BANDS)
def test_an_amount_lands_in_the_right_band_at_every_boundary(grid, amount, sort, label):
    row = grid[f"BAND-{amount}"]
    assert (row["amount_band_sort"], row["amount_band"]) == (sort, label)


@pytest.mark.parametrize(("hour", "part"), DAY_PARTS)
def test_the_day_part_changes_at_6_12_and_18(grid, hour, part):
    row = grid[f"HOUR-{hour}"]
    assert row["hour_of_day"] == hour and row["day_part"] == part


@pytest.mark.parametrize("name", sorted(RULE_CASES))
def test_the_balance_drain_and_night_high_value_flags(grid, name):
    *_, drain, night = RULE_CASES[name]
    assert (grid[name]["flag_balance_drain"], grid[name]["flag_night_high_value"]) == (drain, night)


def test_the_source_rule_flag_needs_both_the_paysim_rule_and_a_decline(grid):
    assert grid["SRC-DECLINED"]["flag_source_rule"] is True
    assert grid["SRC-APPROVED"]["flag_source_rule"] is False
    assert (
        grid["GEN-DECLINED"]["flag_source_rule"] is False
    )  # a real decline is not the source rule
    assert (
        grid["GEN-DECLINED"]["is_declined"] is True and grid["SRC-APPROVED"]["is_declined"] is False
    )


def test_the_date_key_is_the_utc_day_and_columns_pass_through_unchanged(grid):
    row = grid["PASS-THROUGH"]
    assert row["date_key"] == 20261231
    assert (row["amount"], row["channel"], row["source_system"], row["counterparty_id"]) == (
        Decimal("42.10"), "web", "paysim", "MG9",
    )  # fmt: skip
    assert row["is_fraud"] is True and row["type_key"] == 1  # CASH_IN


def test_the_five_known_types_get_their_dimension_keys(spark):
    types = ["CASH_IN", "CASH_OUT", "PAYMENT", "TRANSFER", "DEBIT"]
    rows = build_fact(spark, [silver_txn(event_id=t, customer_id=t, txn_type=t) for t in types])
    assert {r["event_id"]: r["type_key"] for r in rows} == {
        "CASH_IN": 1, "CASH_OUT": 2, "PAYMENT": 3, "TRANSFER": 4, "DEBIT": 5,
    }  # fmt: skip


def test_an_unknown_type_has_no_key_and_trips_a_hard_expectation(grid, spark):
    row = grid["UNKNOWN-TYPE"]
    assert row["type_key"] is None
    expectation = {e.name: e for e in find(load(FACT_FILE), FACT_NAME).expectations}[
        "type_key_present"
    ]
    assert expectation.on_violation == "FAIL UPDATE"
    spark.createDataFrame([(1, None)], "id INT, type_key INT").createOrReplaceTempView("probe")
    assert len(violations(spark, "probe", expectation)) == 1


# ---- the point-in-time join ----

V1_START, V2_START = "2026-09-20 00:00:00", "2026-10-06 12:00:00"
CUSTOMER_VERSIONS = [
    version("CG1", 111, V1_START, V2_START),
    version("CG1", 222, V2_START),
]


def keys_at(spark, *timestamps):
    rows = [
        silver_txn(event_id=ts, customer_id="CG1", counterparty_id="NOBODY", event_ts=ts)
        for ts in timestamps
    ]
    return {
        r["event_id"]: r["origin_customer_key"] for r in build_fact(spark, rows, CUSTOMER_VERSIONS)
    }


def test_an_event_takes_the_version_valid_at_that_moment_and_the_range_is_half_open(spark):
    keys = keys_at(
        spark,
        "2026-09-19 23:59:59",  # before the first version existed
        "2026-09-20 00:00:00",  # exactly when the first version starts: included
        "2026-10-06 11:59:59",  # the last second of the first version
        "2026-10-06 12:00:00",  # exactly when the second starts, and the first ends: second wins
        "2026-10-06 12:00:01",
    )
    assert keys == {
        "2026-09-19 23:59:59": -1,
        "2026-09-20 00:00:00": 111,
        "2026-10-06 11:59:59": 111,
        "2026-10-06 12:00:00": 222,
        "2026-10-06 12:00:01": 222,
    }


def test_sender_and_recipient_are_resolved_separately_at_event_time(spark):
    customers = [
        *CUSTOMER_VERSIONS,
        version("MG7", 777, "2026-09-01 00:00:00", entity_type="merchant"),
    ]
    rows = build_fact(
        spark,
        [silver_txn(customer_id="CG1", counterparty_id="MG7", event_ts="2026-10-06 13:00:00")],
        customers,
    )
    (row,) = rows
    assert (row["origin_customer_key"], row["counterparty_key"]) == (222, 777)


def test_an_id_with_no_dimension_row_maps_to_the_unknown_member_on_both_sides(spark):
    (row,) = build_fact(
        spark, [silver_txn(customer_id="C1231006815", counterparty_id="M1979787155")]
    )
    assert (row["origin_customer_key"], row["counterparty_key"]) == (-1, -1)
    assert row["customer_id"] == "C1231006815"  # the raw id stays on the row


def test_a_customer_with_several_versions_never_multiplies_the_fact_rows(spark):
    versions = [
        version("CG1", 1, "2026-01-01 00:00:00", "2026-03-01 00:00:00"),
        version("CG1", 2, "2026-03-01 00:00:00", "2026-06-01 00:00:00"),
        version("CG1", 3, "2026-06-01 00:00:00"),
    ]
    events = [
        silver_txn(event_id=f"E{i}", customer_id="CG1", event_ts=ts)
        for i, ts in enumerate(
            [
                "2026-01-01 00:00:00",
                "2026-02-28 23:59:59",
                "2026-03-01 00:00:00",
                "2026-07-01 00:00:00",
            ]
        )
    ]
    rows = build_fact(spark, events, versions)
    assert len(rows) == len(events) == 4
    assert [r["origin_customer_key"] for r in sorted(rows, key=lambda r: r["event_id"])] == [
        1,
        1,
        2,
        3,
    ]


# ---- the burst rule ----


def burst_flags(spark, rows):
    return {r["event_id"]: r["flag_burst"] for r in build_fact(spark, rows)}


def transfer(event_id, clock, customer="CG1", kind="TRANSFER"):
    return silver_txn(
        event_id=event_id, customer_id=customer, txn_type=kind, event_ts=f"2026-10-06 {clock}"
    )


def test_five_transfers_within_fifteen_minutes_are_all_a_burst(spark):
    clocks = ["10:00:00", "10:03:00", "10:06:00", "10:09:00", "10:12:00"]
    flags = burst_flags(spark, [transfer(c, c) for c in clocks])
    assert all(flags.values()) and len(flags) == 5


def test_four_transfers_are_not_a_burst(spark):
    clocks = ["10:00:00", "10:01:00", "10:02:00", "10:03:00"]
    assert not any(burst_flags(spark, [transfer(c, c) for c in clocks]).values())


def test_the_window_is_fifteen_minutes_either_side_of_each_row_and_includes_both_ends(spark):
    # Only the 10:15 row sees all five (10:00 and 10:30 sit exactly on its window's edges). If the
    # window were exclusive, or shorter, or one-sided, this row would not be flagged.
    clocks = ["10:00:00", "10:05:00", "10:10:00", "10:15:00", "10:30:00"]
    flags = burst_flags(spark, [transfer(c, c) for c in clocks])
    assert {c for c, flagged in flags.items() if flagged} == {"10:15:00"}


def test_transfers_spread_over_an_hour_are_not_a_burst(spark):
    clocks = ["10:00:00", "10:10:00", "10:20:00", "10:30:00", "10:40:00"]
    assert not any(burst_flags(spark, [transfer(c, c) for c in clocks]).values())


def test_other_customers_transfers_do_not_count_towards_a_burst(spark):
    rows = [transfer(f"a{i}", f"10:0{i}:00", "CG1") for i in range(3)]
    rows += [transfer(f"b{i}", f"10:0{i}:30", "CG2") for i in range(2)]
    assert not any(burst_flags(spark, rows).values())


def test_only_transfers_count_and_only_transfers_are_flagged(spark):
    cash_outs = [transfer(f"c{i}", f"10:0{i}:00", kind="CASH_OUT") for i in range(5)]
    assert not any(burst_flags(spark, cash_outs).values())
    mixed = [transfer(f"t{i}", f"10:0{i}:00") for i in range(4)] + [
        transfer("c", "10:05:00", kind="CASH_OUT")
    ]
    assert not any(burst_flags(spark, mixed).values())  # four transfers, however many neighbours


# ---- the safety net ----


def test_a_normal_set_of_rows_breaks_none_of_the_fact_expectations(spark):
    build_fact(spark, [silver_txn(event_id="A"), silver_txn(event_id="B", txn_type="PAYMENT")])
    register_query(spark, FACT_FILE, FACT_NAME, "fact_rows")
    for expectation in find(load(FACT_FILE), FACT_NAME).expectations:
        assert violations(spark, "fact_rows", expectation) == [], expectation.name
