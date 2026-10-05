"""Silver customer profile events: the change feed that becomes the SCD2 dimension.

The feed has no injected defects, so this layer uses expectations only (ADR-014): a missing key or
sequence value would break the history, so those stop the update, and the rest are measured. The
tests check what passes through and that each expectation notices what it exists for.
"""

from datetime import datetime

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import PROFILE, profile

pytestmark = pytest.mark.sql

FILE = "silver/customer_profile_events.sql"
NAME = "workspace.silver.customer_profile_events"
EXPECTATIONS = {e.name: e for e in find(load(FILE), NAME).expectations}


def events(spark, rows):
    make_table(spark, "workspace.bronze.customer_profile_changes", PROFILE, rows)
    return run_query(spark, FILE, NAME)


def broken_by(spark, rows) -> set[str]:
    make_table(spark, "workspace.bronze.customer_profile_changes", PROFILE, rows)
    register_query(spark, FILE, NAME, "events")
    return {name for name, e in EXPECTATIONS.items() if violations(spark, "events", e)}


def test_each_change_event_passes_through_with_its_time(spark):
    rows = events(
        spark,
        [
            profile("CG1", "2026-09-20T00:00:00Z", "standard"),
            profile("CG1", "2026-10-06T12:00:00Z", "premium"),
        ],
    )
    assert sorted((r["customer_id"], r["segment"], r["changed_at"]) for r in rows) == [
        ("CG1", "premium", datetime(2026, 10, 6, 12)),
        ("CG1", "standard", datetime(2026, 9, 20)),
    ]


def test_the_audit_columns_and_the_rescue_column_are_not_carried_forward(spark):
    (row,) = events(spark, [profile("CG1", "2026-09-20T00:00:00Z")])
    assert set(row) == {
        "customer_id", "entity_type", "segment", "region", "changed_at",
        "_source_file", "_ingested_at",
    }  # fmt: skip


def test_clean_customer_and_merchant_events_break_no_expectation(spark):
    rows = [
        profile("CG1", "2026-09-20T00:00:00Z"),
        profile("MG1", "2026-09-20T00:00:00Z", kind="merchant"),
    ]
    assert broken_by(spark, rows) == set()


@pytest.mark.parametrize(
    ("overrides", "broken"),
    [
        ({"customer_id": None}, {"customer_id_present", "id_prefix_matches_type"}),
        ({"changed_at": None}, {"changed_at_present"}),
        ({"entity_type": "household"}, {"known_entity_type", "id_prefix_matches_type"}),
        ({"customer_id": "MG1"}, {"id_prefix_matches_type"}),  # a merchant id on a customer
        ({"segment": None}, {"segment_present"}),
        ({"region": None}, {"region_present"}),
    ],
    ids=lambda v: str(v),
)
def test_each_expectation_notices_what_it_exists_for(spark, overrides, broken):
    row = profile("CG1", "2026-09-20T00:00:00Z")
    row.update(overrides)
    assert broken_by(spark, [row]) == broken


def test_only_a_missing_key_or_time_stops_the_update(spark):
    # Without a customer id or a change time the history cannot be built, so these two stop the
    # update. Everything else is measured and left for a person to look at.
    hard = {name for name, e in EXPECTATIONS.items() if e.on_violation == "FAIL UPDATE"}
    assert hard == {"customer_id_present", "changed_at_present"}
