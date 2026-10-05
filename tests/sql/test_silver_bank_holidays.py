"""Silver bank holidays: the GOV.UK payload parsed into one row per region, date and title.

Bronze keeps the payload exactly as received and silver parses it (ADR-009), so these tests feed
silver payloads shaped like the real API response and check what comes out.
"""

import json
from datetime import date, datetime

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import BANK_HOLIDAYS_RAW

pytestmark = pytest.mark.sql

FILE = "silver/bank_holidays.sql"


def event(title, day, notes="", bunting=True):
    return {"title": title, "date": day, "notes": notes, "bunting": bunting}


def payload(**divisions) -> str:
    """A payload shaped like the API's: a map of region name to {division, events}."""
    return json.dumps(
        {
            name.replace("_", "-"): {"division": name.replace("_", "-"), "events": events}
            for name, events in divisions.items()
        }
    )


def landed(text, at):
    return {
        "payload": text,
        "_source_file": f"bank_holidays_{at[:10]}.json",
        "_source_modified_at": at,
        "_ingested_at": at,
    }


FIRST = payload(
    england_and_wales=[event("New Year's Day", "2026-01-01"), event("Christmas Day", "2026-12-25")],
    scotland=[
        event("2nd January", "2026-01-02", bunting=False),
        event("Christmas Day", "2026-12-25"),
    ],
    northern_ireland=[event("St Patrick's Day", "2026-03-17", notes="Substitute day")],
)


def parse_holidays(spark, *payloads):
    make_table(spark, "workspace.bronze.bank_holidays_raw", BANK_HOLIDAYS_RAW, list(payloads))
    return run_query(spark, FILE, "workspace.silver.bank_holidays")


def keyed(rows):
    return {(r["division"], r["holiday_date"], r["title"]): r for r in rows}


def test_one_payload_gives_one_row_per_region_date_and_title(spark):
    rows = parse_holidays(spark, landed(FIRST, "2026-10-04 09:00:00"))
    assert sorted(keyed(rows)) == [
        ("england-and-wales", date(2026, 1, 1), "New Year's Day"),
        ("england-and-wales", date(2026, 12, 25), "Christmas Day"),
        ("northern-ireland", date(2026, 3, 17), "St Patrick's Day"),
        ("scotland", date(2026, 1, 2), "2nd January"),
        ("scotland", date(2026, 12, 25), "Christmas Day"),
    ]


def test_each_row_carries_the_notes_the_bunting_flag_and_when_it_was_loaded(spark):
    rows = keyed(parse_holidays(spark, landed(FIRST, "2026-10-04 09:00:00")))
    ni = rows[("northern-ireland", date(2026, 3, 17), "St Patrick's Day")]
    assert ni["notes"] == "Substitute day" and ni["bunting"] is True
    assert rows[("scotland", date(2026, 1, 2), "2nd January")]["bunting"] is False
    assert ni["loaded_at"] == datetime(2026, 10, 4, 9, 0)


def complete(**changes):
    """The first payload with some regions replaced: a newer payload is the whole calendar again."""
    calendar = json.loads(FIRST)
    for name, events in changes.items():
        key = name.replace("_", "-")
        calendar[key] = {"division": key, "events": events}
    return json.dumps(calendar)


def test_the_same_holiday_in_two_payloads_appears_once_with_the_newest_values(spark):
    newer = complete(
        england_and_wales=[
            event("New Year's Day", "2026-01-01", notes="Corrected note"),
            event("Christmas Day", "2026-12-25"),
        ]
    )
    rows = parse_holidays(
        spark, landed(FIRST, "2026-10-04 09:00:00"), landed(newer, "2026-10-05 09:00:00")
    )
    new_year = [r for r in rows if r["title"] == "New Year's Day"]
    assert len(new_year) == 1
    assert new_year[0]["notes"] == "Corrected note"
    assert new_year[0]["loaded_at"] == datetime(2026, 10, 5, 9, 0)
    assert len(rows) == 5


def test_a_holiday_that_is_new_in_a_later_payload_is_added(spark):
    newer = complete(
        england_and_wales=[
            event("New Year's Day", "2026-01-01"),
            event("Christmas Day", "2026-12-25"),
            event("Coronation holiday", "2027-05-08"),
        ]
    )
    rows = parse_holidays(
        spark, landed(FIRST, "2026-10-04 09:00:00"), landed(newer, "2026-10-05 09:00:00")
    )
    assert ("england-and-wales", date(2027, 5, 8), "Coronation holiday") in keyed(rows)
    assert len(rows) == 6


# FINDING (Phase 6, fixed): the file used to keep "the most recent row for each holiday", which is
# not the same as "the newest payload is the truth". If GOV.UK moved a holiday, the OLD date stayed
# in the table, flagged as a bank holiday for ever. Now the newest payload is the calendar.
def test_a_holiday_missing_from_the_newest_payload_is_gone(spark):
    newer = complete(england_and_wales=[event("Christmas Day", "2026-12-25")])
    rows = parse_holidays(
        spark, landed(FIRST, "2026-10-04 09:00:00"), landed(newer, "2026-10-05 09:00:00")
    )
    assert ("england-and-wales", date(2026, 1, 1), "New Year's Day") not in keyed(rows)
    assert ("england-and-wales", date(2026, 12, 25), "Christmas Day") in keyed(rows)


def test_a_moved_holiday_is_flagged_on_its_new_date_only(spark):
    older = complete(england_and_wales=[event("Spring bank holiday", "2022-05-30")])
    newer = complete(england_and_wales=[event("Spring bank holiday", "2022-06-02")])
    rows = parse_holidays(
        spark, landed(older, "2026-10-04 09:00:00"), landed(newer, "2026-10-05 09:00:00")
    )
    spring = [r["holiday_date"] for r in rows if r["title"] == "Spring bank holiday"]
    assert spring == [date(2022, 6, 2)]


def test_a_payload_that_lists_a_holiday_twice_still_gives_one_row(spark):
    twice = complete(england_and_wales=[event("Christmas Day", "2026-12-25")] * 2)
    rows = parse_holidays(spark, landed(twice, "2026-10-04 09:00:00"))
    assert len([r for r in rows if r["division"] == "england-and-wales"]) == 1


def test_a_region_the_pipeline_does_not_know_trips_a_hard_expectation(spark):
    odd = payload(wales=[event("Eisteddfod", "2026-08-01")])
    make_table(
        spark,
        "workspace.bronze.bank_holidays_raw",
        BANK_HOLIDAYS_RAW,
        [landed(odd, "2026-10-04 09:00:00")],
    )
    register_query(spark, FILE, "workspace.silver.bank_holidays", "parsed")
    expectations = {
        e.name: e for e in find(load(FILE), "workspace.silver.bank_holidays").expectations
    }
    assert [v["division"] for v in violations(spark, "parsed", expectations["known_division"])] == [
        "wales"
    ]
    assert expectations["known_division"].on_violation == "FAIL UPDATE"


def test_a_clean_payload_breaks_none_of_the_expectations(spark):
    make_table(
        spark,
        "workspace.bronze.bank_holidays_raw",
        BANK_HOLIDAYS_RAW,
        [landed(FIRST, "2026-10-04 09:00:00")],
    )
    register_query(spark, FILE, "workspace.silver.bank_holidays", "parsed")
    for expectation in find(load(FILE), "workspace.silver.bank_holidays").expectations:
        assert violations(spark, "parsed", expectation) == [], expectation.name


def test_a_holiday_with_no_title_is_measured_not_stopped(spark):
    odd = payload(england_and_wales=[event("", "2026-08-31")])
    make_table(
        spark,
        "workspace.bronze.bank_holidays_raw",
        BANK_HOLIDAYS_RAW,
        [landed(odd, "2026-10-04 09:00:00")],
    )
    register_query(spark, FILE, "workspace.silver.bank_holidays", "parsed")
    expectations = {
        e.name: e for e in find(load(FILE), "workspace.silver.bank_holidays").expectations
    }
    assert len(violations(spark, "parsed", expectations["title_present"])) == 1
    assert expectations["title_present"].on_violation == "WARN"


def test_a_malformed_date_fails_the_query_loudly_instead_of_becoming_null(spark):
    # With ANSI mode on, to_date of text that is not a date raises. For reference data that is the
    # right outcome: a changed API shape stops the update and gets noticed.
    broken = payload(england_and_wales=[event("Mystery", "not-a-date")])
    make_table(
        spark,
        "workspace.bronze.bank_holidays_raw",
        BANK_HOLIDAYS_RAW,
        [landed(broken, "2026-10-04 09:00:00")],
    )
    with pytest.raises(Exception, match="(?i)not-a-date|cast|parse"):
        run_query(spark, FILE, "workspace.silver.bank_holidays")
