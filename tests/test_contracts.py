"""Contract tests: facts that live in more than one file, checked against each other.

Something stated in two places (the five transaction types, the last day of the calendar, the
columns of the feed) drifts apart as soon as one place is edited. The Phase 5 calendar bug was
exactly that. Each test reads the same fact from every file that holds it and compares them, so a
change in one place fails until the others follow.
"""

import importlib
import json
import re
import tomllib
from datetime import date
from pathlib import Path

from payments_lakehouse.generator import REQUIRED_FIELDS, TYPES, GeneratorConfig, _day_files
from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import contract

REPO = Path(__file__).resolve().parents[1]
CHECKS = REPO / "sql" / "checks"


def quoted(text: str) -> set[str]:
    return set(re.findall(r"'([A-Z_]+)'", text))


def column_names(ddl: str) -> list[str]:
    """Column names from a DDL string, splitting only on the commas outside parentheses."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(ddl):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(ddl[start:i].strip())
            start = i + 1
    parts.append(ddl[start:].strip())
    return [p.split()[0] for p in parts if p]


def unify_queries() -> dict[str, str]:
    statements = load("silver/transactions_unified.sql")
    return {n: find(statements, n).query for n in ("unify_generated", "unify_paysim")}


# ---- the five transaction types and the two statuses ----


def test_the_five_transaction_types_are_the_same_in_every_file():
    known = set(TYPES)
    assert len(known) == 5
    for name, query in unify_queries().items():
        listed = re.findall(r"\btype\s+NOT IN \(([^)]*)\)", query)
        assert len(listed) == 1, f"{name} should list the types once"
        assert quoted(listed[0]) == known, name
    silver = find(load("silver/transactions.sql"), "workspace.silver.transactions")
    known_type = {e.name: e for e in silver.expectations}["known_type"]
    assert quoted(known_type.condition) == known
    dim_type = find(load("gold/dim_type.sql"), "dim_type")
    assert set(re.findall(r"\(\d+,\s*'([A-Z_]+)'", dim_type.query)) == known


def test_the_two_statuses_are_the_same_in_the_unify_rule_and_the_safety_net_and_cover_the_feed():
    in_rule = re.findall(r"\bstatus\s+NOT IN \(([^)]*)\)", unify_queries()["unify_generated"])
    silver = find(load("silver/transactions.sql"), "workspace.silver.transactions")
    in_net = {e.name: e for e in silver.expectations}["known_status"].condition
    assert quoted(in_rule[0]) == quoted(in_net) == {"APPROVED", "DECLINED"}
    texts, _ = _day_files(GeneratorConfig(), date(2026, 9, 21))
    produced = {json.loads(line)["status"] for line in texts["transactions"].splitlines()}
    assert produced <= {"APPROVED", "DECLINED"}


def test_the_unify_rule_treats_every_field_the_generator_can_blank_as_required():
    query = unify_queries()["unify_generated"]
    condition = re.search(r"WHEN(.*?)THEN 'NULL_REQUIRED_FIELD'", query, re.DOTALL).group(1)
    required = set(re.findall(r"\bt\.(\w+) IS NULL", condition))
    assert set(REQUIRED_FIELDS) <= required
    assert {"event_id", "status"} <= required  # the key and the status are required too


# ---- the calendar and the date bounds ----


def test_the_calendar_ends_where_the_silver_date_bound_ends_and_the_check_counts_its_days():
    calendar = find(load("gold/dim_date.sql"), "dim_date").query
    first, last = (
        date.fromisoformat(d)
        for d in re.search(r"SEQUENCE\(DATE '([\d-]+)', DATE '([\d-]+)'", calendar).groups()
    )
    silver = find(load("silver/transactions.sql"), "workspace.silver.transactions")
    in_range = {e.name: e for e in silver.expectations}["event_date_in_range"].condition
    low, high = (date.fromisoformat(d) for d in re.findall(r"DATE '([\d-]+)'", in_range))
    assert high == last, "silver accepts dates the calendar cannot hold, or the reverse"
    assert first <= low, "the calendar must start no later than silver's earliest date"
    expected_days = int(
        re.search(
            r"'dim_date: calendar days' AS check_name,\s*(\d+) AS expected",
            (CHECKS / "gold_reconciliation.sql").read_text(),
        ).group(1)
    )
    assert expected_days == (last - first).days + 1


def test_silver_accepts_dates_from_the_paysim_anchor_and_the_generator_starts_after_it():
    unified = unify_queries()["unify_paysim"]
    anchor = date.fromisoformat(re.search(r"TIMESTAMP '([\d-]+) ", unified).group(1))
    silver = find(load("silver/transactions.sql"), "workspace.silver.transactions")
    in_range = {e.name: e for e in silver.expectations}["event_date_in_range"].condition
    low = date.fromisoformat(re.findall(r"DATE '([\d-]+)'", in_range)[0])
    assert low == anchor  # ADR-008: step 1 of PaySim is the first day silver accepts
    assert GeneratorConfig().initial_date >= anchor


# ---- the feed and the bronze contract ----


def feed_keys(day: date) -> set[str]:
    texts, _ = _day_files(GeneratorConfig(), day)
    return {key for line in texts["transactions"].splitlines() for key in json.loads(line)}


def test_the_generated_feed_matches_the_bronze_contract_apart_from_the_two_planned_new_columns():
    declared = set(column_names(contract("transactions_daily", "HINTS")))
    cfg = GeneratorConfig()
    before = feed_keys(cfg.channel_from.replace(day=cfg.channel_from.day - 1))
    after = feed_keys(cfg.device_from)
    assert before == declared, "the feed and the schema hints disagree before any change"
    assert after - before == {"channel", "device_type"}  # added by schema evolution, not declared
    assert before <= after


def test_the_profile_feed_header_is_the_bronze_profile_contract():
    texts, _ = _day_files(GeneratorConfig(), date(2026, 9, 21))
    header = texts["profile"].splitlines()[0].split(",")
    declared = [c for c in column_names(contract("customer_profile_changes", "SCHEMA"))]
    assert header == [c for c in declared if c != "_rescued_data"]


# ---- the reconciliation suites ----

SUITE_FLOORS = {"bronze": 19, "silver": 26, "gold": 22}


def test_no_reconciliation_check_has_quietly_gone_missing_and_each_suite_has_one_shape():
    for layer, floor in SUITE_FLOORS.items():
        text = (CHECKS / f"{layer}_reconciliation.sql").read_text()
        names = re.findall(r"'([^']+)' AS check_name", text)
        assert len(names) == len(set(names)), f"{layer}: two checks share a name"
        assert len(names) >= floor, f"{layer}: {len(names)} checks, was {floor}"
        assert re.search(
            r"SELECT\s+check_name,\s+expected,\s+actual,"
            r"\s+expected = actual AS passed\s+FROM checks",
            text,
        ), f"{layer}: the final SELECT must return check_name, expected, actual, passed"


# ---- the commands ----


def test_every_console_script_points_at_a_function_that_exists():
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert {"pull-holidays", "generate-day", "run-checks"} <= set(scripts)
    for name, target in scripts.items():
        module, function = target.split(":")
        assert callable(getattr(importlib.import_module(module), function)), name
