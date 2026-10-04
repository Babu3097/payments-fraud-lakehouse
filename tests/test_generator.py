import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import pytest

from payments_lakehouse.generator import (
    CHANNELS,
    DEVICES,
    REQUIRED_FIELDS,
    TS_FORMAT,
    TYPES,
    GeneratorConfig,
    entity_state,
    landing_main,
    profile_events_for_day,
    resolve_date,
    transactions_for_day,
    write_day,
    write_day_to_landing,
)

# Small population and volume keep the tests fast; the logic is the same as production sizes.
CFG = GeneratorConfig(rows_per_day=10_000, customers=2_000, merchants=200)
DAY = date(2026, 9, 21)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def parse_ts(value):
    try:
        return datetime.strptime(value, TS_FORMAT)
    except (TypeError, ValueError):
        return None


def test_same_day_and_seed_give_byte_identical_files(tmp_path):
    write_day(CFG, DAY, tmp_path / "a")
    write_day(CFG, DAY, tmp_path / "b")
    names = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert names == [
        f"customer_profile_{DAY}.csv",
        f"manifest_{DAY}.json",
        f"transactions_{DAY}.jsonl",
    ]
    for name in names:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_a_different_seed_gives_different_transactions(tmp_path):
    write_day(CFG, DAY, tmp_path / "a")
    write_day(
        GeneratorConfig(seed=7, rows_per_day=10_000, customers=2_000, merchants=200),
        DAY,
        tmp_path / "b",
    )
    name = f"transactions_{DAY}.jsonl"
    assert (tmp_path / "a" / name).read_bytes() != (tmp_path / "b" / name).read_bytes()


def test_manifest_matches_independent_counts_from_the_file(tmp_path):
    manifest = write_day(CFG, DAY, tmp_path)
    rows = read_rows(tmp_path / f"transactions_{DAY}.jsonl")
    defects = manifest["defects"]

    assert len(rows) == manifest["rows_written"] == CFG.rows_per_day + defects["duplicates"]
    ids = Counter(row["event_id"] for row in rows)
    assert sum(count - 1 for count in ids.values()) == defects["duplicates"]
    assert len(ids) == CFG.rows_per_day

    assert (
        sum(any(row[f] is None for f in REQUIRED_FIELDS) for row in rows) == defects["null_field"]
    )
    assert (
        sum(isinstance(r["amount"], int | float) and r["amount"] < 0 for r in rows)
        == defects["bad_amount"]
    )
    assert (
        sum(r["type"] is not None and r["type"] not in TYPES for r in rows)
        == defects["unknown_type"]
    )
    bad_ts = sum(r["event_ts"] is not None and parse_ts(r["event_ts"]) is None for r in rows)
    assert bad_ts == defects["bad_timestamp"]

    unique = {row["event_id"]: row for row in rows}.values()
    assert sum(row["is_fraud"] for row in unique) == manifest["fraud_rows"]
    assert sum(row["status"] == "DECLINED" for row in unique) == manifest["declined_rows"]


def test_only_fraud_rows_empty_the_whole_origin_balance():
    rows, _ = transactions_for_day(CFG, DAY)
    drains = [
        r
        for r in rows
        if r["type"] in ("TRANSFER", "CASH_OUT")
        and isinstance(r["amount"], int | float)
        and r["amount"] > 0
        and r["amount"] == r["origin_balance_before"]
    ]
    assert drains
    assert all(r["is_fraud"] == 1 for r in drains)


def test_fraud_bursts_send_several_transfers_within_fifteen_minutes():
    rows, _ = transactions_for_day(CFG, DAY)
    by_customer = defaultdict(list)
    for r in rows:
        stamp = parse_ts(r["event_ts"])
        if r["is_fraud"] and r["type"] == "TRANSFER" and r["customer_id"] and stamp:
            by_customer[r["customer_id"]].append(stamp)
    window = timedelta(minutes=15)
    bursts = [
        customer
        for customer, stamps in by_customer.items()
        if any(b - a <= window for a, b in zip(sorted(stamps), sorted(stamps)[4:], strict=False))
    ]
    assert bursts


def test_channel_field_appears_only_from_the_schema_change_date():
    before, _ = transactions_for_day(CFG, CFG.channel_from - timedelta(days=1))
    after, manifest = transactions_for_day(CFG, CFG.channel_from)
    assert all("channel" not in row for row in before)
    assert manifest["has_channel"]
    assert all(row["channel"] in CHANNELS for row in after)


def test_generated_ids_never_collide_with_paysim_ids():
    rows, _ = transactions_for_day(CFG, DAY)
    generated = re.compile(r"CG\d{7}|MG\d{6}")
    paysim = re.compile(r"[CM]\d+")
    for row in rows:
        for field in ("customer_id", "counterparty_id"):
            value = row[field]
            if value is not None:
                assert generated.fullmatch(value)
                assert not paysim.fullmatch(value)


def test_initial_profile_load_covers_every_customer_and_merchant():
    events = profile_events_for_day(CFG, CFG.initial_date)
    assert len(events) == CFG.customers + CFG.merchants
    assert {e.entity_type for e in events} == {"customer", "merchant"}
    assert len({e.customer_id for e in events}) == len(events)


def test_daily_profile_changes_alter_exactly_one_attribute_on_their_own_day():
    before = entity_state(CFG, DAY - timedelta(days=1))
    events = profile_events_for_day(CFG, DAY)
    assert len(events) == round((CFG.customers + CFG.merchants) * CFG.daily_change_rate)
    for event in events:
        _, old_segment, old_region = before[event.customer_id]
        changed = (event.segment != old_segment) + (event.region != old_region)
        assert changed == 1
        assert event.changed_at.date() == DAY


def test_a_day_before_the_initial_load_is_rejected():
    with pytest.raises(ValueError, match="before the initial load"):
        profile_events_for_day(CFG, CFG.initial_date - timedelta(days=1))


def test_the_device_field_appears_only_from_its_change_date():
    before, _ = transactions_for_day(CFG, CFG.device_from - timedelta(days=1))
    after, _ = transactions_for_day(CFG, CFG.device_from)
    assert all("device_type" not in row for row in before)
    assert all(row["device_type"] in DEVICES for row in after)
    # The earlier channel field is still there: schema changes accumulate.
    assert all(row["channel"] in CHANNELS for row in after)


def test_days_before_the_device_change_are_unchanged_by_it(tmp_path):
    # A day before the change is identical whether or not the change exists in the config.
    later = GeneratorConfig(
        rows_per_day=10_000, customers=2_000, merchants=200, device_from=date(2099, 1, 1)
    )
    write_day(CFG, DAY, tmp_path / "a")
    write_day(later, DAY, tmp_path / "b")
    name = f"transactions_{DAY}.jsonl"
    assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("yesterday", date(2026, 10, 3)),
        ("today", date(2026, 10, 4)),
        ("2026-09-25", date(2026, 9, 25)),
    ],
)
def test_resolve_date(spec, expected):
    assert resolve_date(spec, today=date(2026, 10, 4)) == expected


def test_landing_files_land_in_the_folders_their_loaders_read_with_the_same_bytes(tmp_path):
    write_day(CFG, DAY, tmp_path / "flat")
    written = write_day_to_landing(CFG, DAY, tmp_path / "landing")
    assert all(written.values())
    flat, landing = tmp_path / "flat", tmp_path / "landing"
    pairs = {
        f"transactions_{DAY}.jsonl": "transactions_daily",
        f"customer_profile_{DAY}.csv": "customer_profile",
        f"manifest_{DAY}.json": "_manifests",
    }
    for name, folder in pairs.items():
        assert (landing / folder / name).read_bytes() == (flat / name).read_bytes()


def test_writing_a_day_again_leaves_every_file_untouched(tmp_path):
    first = write_day_to_landing(CFG, DAY, tmp_path)
    paths = [p for p in tmp_path.rglob("*") if p.is_file()]
    modified = {p: p.stat().st_mtime_ns for p in paths}
    second = write_day_to_landing(CFG, DAY, tmp_path)
    assert all(first.values())
    assert not any(second.values())
    assert {p: p.stat().st_mtime_ns for p in paths} == modified
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_file_whose_content_differs_is_rewritten(tmp_path):
    write_day_to_landing(CFG, DAY, tmp_path)
    target = tmp_path / "transactions_daily" / f"transactions_{DAY}.jsonl"
    target.write_text("tampered")
    written = write_day_to_landing(CFG, DAY, tmp_path)
    assert written[f"transactions_{DAY}.jsonl"] is True
    assert target.read_text() != "tampered"


def test_the_job_entry_point_writes_then_reports_unchanged(tmp_path, capsys):
    landing_main(["--landing", str(tmp_path), "--date", "2026-09-21"])
    first = capsys.readouterr().out
    landing_main(["--landing", str(tmp_path), "--date", "2026-09-21"])
    second = capsys.readouterr().out
    assert first.count("wrote:") == 3
    assert second.count("unchanged:") == 3


def test_the_job_entry_point_refuses_a_date_before_the_initial_load(tmp_path):
    with pytest.raises(SystemExit, match="before the initial load"):
        landing_main(["--landing", str(tmp_path), "--date", "2026-09-19"])


def test_yesterday_is_measured_from_the_date_the_job_passes_in(tmp_path, capsys):
    # A repair run on a later day gets the same --today, so it lands the same files.
    landing_main(["--landing", str(tmp_path), "--date", "yesterday", "--today", "2026-09-22"])
    landing_main(["--landing", str(tmp_path), "--date", "yesterday", "--today", "2026-09-22"])
    out = capsys.readouterr().out
    assert "transactions_2026-09-21.jsonl" in out
    assert out.count("unchanged:") == 3
    assert sorted(p.name for p in (tmp_path / "transactions_daily").iterdir()) == [
        "transactions_2026-09-21.jsonl"
    ]


def test_an_unresolved_job_reference_fails_instead_of_picking_a_day(tmp_path):
    # If the platform ever passed the placeholder through literally, the task must fail loudly.
    with pytest.raises(SystemExit):
        landing_main(["--landing", str(tmp_path), "--today", "{{job.start_time.iso_date}}"])
