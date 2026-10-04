"""Fixture schemas and row builders for the SQL tests.

Each bronze schema comes from the loader that declares it (see tests/helpers/spark_support.py), plus
the columns Auto Loader adds. A builder returns one clean row, and a test overrides only the field
it is about, so the field under test is the only thing that differs.
"""

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import contract

AUDIT = "_source_file STRING, _source_modified_at TIMESTAMP, _ingested_at TIMESTAMP"

# `channel` is the column that appeared on 2026-09-28 (ADR-012), so it is not in the hints.
TRANSACTIONS_DAILY = (
    contract("transactions_daily", "HINTS") + ", channel STRING, _rescued_data STRING, " + AUDIT
)
PAYSIM = contract("paysim_transactions", "SCHEMA") + ", " + AUDIT
PROFILE = contract("customer_profile_changes", "SCHEMA") + ", " + AUDIT
BANK_HOLIDAYS_RAW = "payload STRING, " + AUDIT
BANK_HOLIDAYS = (
    "division STRING, title STRING, notes STRING, bunting BOOLEAN, "
    "loaded_at TIMESTAMP, holiday_date DATE"
)


def txn(**overrides) -> dict:
    """One clean row of the generated daily feed, as bronze stores it."""
    row = {
        "event_id": "T20261006000001",
        "event_ts": "2026-10-06T10:30:00Z",
        "type": "PAYMENT",
        "amount": "25.50",
        "customer_id": "CG0000001",
        "counterparty_id": "MG000001",
        "origin_balance_before": "100.00",
        "origin_balance_after": "74.50",
        "status": "APPROVED",
        "decline_reason": None,
        "is_fraud": 0,
        "source_system": "generator-v1",
        "channel": "app",
        "_rescued_data": None,
        "_source_file": "transactions_2026-10-06.jsonl",
        "_source_modified_at": "2026-10-07 06:00:00",
        "_ingested_at": "2026-10-07 06:01:00",
    }
    row.update(overrides)
    return row


def paysim(**overrides) -> dict:
    """One clean PaySim row (the first row of the real file), as bronze stores it."""
    row = {
        "step": 1,
        "type": "PAYMENT",
        "amount": "9839.64",
        "nameOrig": "C1231006815",
        "oldbalanceOrg": "170136.00",
        "newbalanceOrig": "160296.36",
        "nameDest": "M1979787155",
        "oldbalanceDest": "0.00",
        "newbalanceDest": "0.00",
        "isFraud": 0,
        "isFlaggedFraud": 0,
        "_rescued_data": None,
        "_source_file": "PS_20174392719_1491204439457_log.csv",
        "_source_modified_at": "2026-10-04 09:00:00",
        "_ingested_at": "2026-10-04 09:01:00",
    }
    row.update(overrides)
    return row


def holiday(day: str, division: str = "england-and-wales", title: str = "A holiday") -> dict:
    """One row of silver.bank_holidays."""
    return {
        "division": division,
        "title": title,
        "notes": "",
        "bunting": True,
        "loaded_at": "2026-10-04 09:00:00",
        "holiday_date": day,
    }


# ---- silver and gold fixtures ------------------------------------------------------------------


def _drop_columns(ddl: str, names: set[str]) -> str:
    """Remove columns from a DDL string, splitting only on the commas outside parentheses."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(ddl):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(ddl[start:i].strip())
            start = i + 1
    parts.append(ddl[start:].strip())
    return ", ".join(p for p in parts if p.split()[0] not in names)


# silver.transactions is the private working table minus the two columns the CDC flow leaves out
# (COLUMNS * EXCEPT (failed_checks, event_ts_raw)), so its schema is read from the pipeline itself.
SILVER_TRANSACTIONS = _drop_columns(
    find(load("silver/transactions_unified.sql"), "transactions_unified").columns,
    {"failed_checks", "event_ts_raw"},
)
DIM_CUSTOMER = (
    "customer_key BIGINT, customer_id STRING, entity_type STRING, segment STRING, region STRING, "
    "valid_from TIMESTAMP, valid_to TIMESTAMP, is_current BOOLEAN"
)
CUSTOMER_HISTORY = (
    "customer_id STRING, entity_type STRING, segment STRING, region STRING, changed_at TIMESTAMP, "
    "__start_at TIMESTAMP, __end_at TIMESTAMP"
)
FACT = (
    "event_id STRING, event_ts TIMESTAMP, date_key INT, hour_of_day INT, type_key INT, "
    "customer_id STRING, counterparty_id STRING, amount DECIMAL(18,2), amount_band_sort INT, "
    "amount_band STRING, origin_balance_before DECIMAL(18,2), status STRING, is_fraud BOOLEAN, "
    "source_system STRING, day_part STRING, is_declined BOOLEAN, flag_balance_drain BOOLEAN, "
    "flag_night_high_value BOOLEAN, flag_burst BOOLEAN, flag_source_rule BOOLEAN"
)


def silver_txn(**overrides) -> dict:
    """One clean row of silver.transactions."""
    row = {
        "event_id": "E1",
        "event_ts": "2026-10-06 10:00:00",
        "event_date": "2026-10-06",
        "txn_type": "TRANSFER",
        "amount": "100.00",
        "customer_id": "CG0000001",
        "counterparty_id": "CG0000002",
        "counterparty_kind": "customer",
        "origin_balance_before": "500.00",
        "origin_balance_after": "400.00",
        "status": "APPROVED",
        "status_source": "source_system",
        "is_fraud": False,
        "is_zero_amount": False,
        "is_bank_holiday": False,
        "channel": "app",
        "source_system": "generator-v1",
        "_source_file": "transactions_2026-10-06.jsonl",
        "_ingested_at": "2026-10-07 06:01:00",
    }
    row.update(overrides)
    return row


def version(customer_id, key, valid_from, valid_to="9999-12-31 00:00:00", **overrides) -> dict:
    """One version row of gold.dim_customer."""
    row = {
        "customer_key": key,
        "customer_id": customer_id,
        "entity_type": "customer",
        "segment": "standard",
        "region": "London",
        "valid_from": valid_from,
        "valid_to": valid_to,
        "is_current": valid_to.startswith("9999"),
    }
    row.update(overrides)
    return row


def fact(**overrides) -> dict:
    """One row of gold.fact_transactions, with only the columns the KPI views read."""
    row = {
        "event_id": "F1",
        "event_ts": "2026-10-06 10:00:00",
        "date_key": 20261006,
        "hour_of_day": 10,
        "type_key": 4,
        "customer_id": "CG0000001",
        "counterparty_id": "CG0000002",
        "amount": "100.00",
        "amount_band_sort": 3,
        "amount_band": "100 to 999.99",
        "origin_balance_before": "500.00",
        "status": "APPROVED",
        "is_fraud": False,
        "source_system": "generator-v1",
        "day_part": "morning",
        "is_declined": False,
        "flag_balance_drain": False,
        "flag_night_high_value": False,
        "flag_burst": False,
        "flag_source_rule": False,
    }
    row.update(overrides)
    return row


def profile(customer_id, changed_at, segment="standard", region="London", kind="customer") -> dict:
    """One row of bronze.customer_profile_changes."""
    return {
        "customer_id": customer_id,
        "entity_type": kind,
        "segment": segment,
        "region": region,
        "changed_at": changed_at,
        "_rescued_data": None,
        "_source_file": "customer_profile_2026-10-06.csv",
        "_source_modified_at": "2026-10-07 06:00:00",
        "_ingested_at": "2026-10-07 06:01:00",
    }
