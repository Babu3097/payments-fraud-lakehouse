"""Deterministic generator for the daily transaction and customer-profile feeds.

Every day is seeded from (seed, date), so regenerating a day gives byte-identical files. That is
what makes reruns idempotent and lets tests assert exact counts. Standard library only, so there
is nothing extra to install on a laptop or in CI. See docs/decisions.md (ADR-005, ADR-006).

Each day produces three files:
- transactions_<date>.jsonl      JSON Lines, one transaction per line (with injected defects)
- customer_profile_<date>.csv    customer/merchant change events (initial load, then daily changes)
- manifest_<date>.json           the ground truth: exact counts of rows, fraud and each defect
"""

import argparse
import csv
import io
import json
import os
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import accumulate
from pathlib import Path

TYPES = ("CASH_OUT", "PAYMENT", "CASH_IN", "TRANSFER", "DEBIT")
# Type mix and hour-of-day shape are taken from the PaySim profile (docs/data_profile.md).
TYPE_CUM = tuple(accumulate((35.2, 33.8, 22.0, 8.4, 0.65)))
HOUR_CUM = tuple(
    accumulate(
        (27, 9, 2, 1, 1, 3, 8, 26, 283, 425, 445, 483, 468, 439, 416, 441, 439, 580, 647, 553)
        + (247, 194, 141, 71)
    )
)
# Lognormal (mu, sigma) per type; the median amount is exp(mu).
AMOUNT_SHAPE = {
    "CASH_OUT": (5.0, 1.0),
    "PAYMENT": (3.4, 0.9),
    "CASH_IN": (5.2, 1.0),
    "TRANSFER": (5.7, 1.3),
    "DEBIT": (2.7, 0.7),
}
REGIONS = (
    "North East",
    "North West",
    "Yorkshire and The Humber",
    "East Midlands",
    "West Midlands",
    "East of England",
    "London",
    "South East",
    "South West",
    "Wales",
    "Scotland",
    "Northern Ireland",
)
CUSTOMER_SEGMENTS = ("mass_market", "premium", "student", "small_business")
CUSTOMER_SEGMENT_WEIGHTS = (60, 15, 15, 10)
MERCHANT_SEGMENTS = ("retail", "food_and_drink", "travel", "utilities")
CHANNELS = ("app", "agent", "pos")
DECLINE_REASONS = ("INSUFFICIENT_FUNDS", "LIMIT_EXCEEDED", "CARD_BLOCKED")
BAD_TYPES = ("REFUND", "CHARGEBACK", "ADJUSTMENT")
BAD_TIMESTAMPS = ("not-a-timestamp", "2026-13-45T99:99:99Z")
REQUIRED_FIELDS = ("customer_id", "amount", "type", "event_ts")
PROFILE_COLUMNS = ("customer_id", "entity_type", "segment", "region", "changed_at")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
BURST_SIZE = 6  # transfers in one fraud burst, all within about ten minutes
MULE_COUNT = 20  # fixed recipient accounts that receive most fraudulent transfers


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 2026
    rows_per_day: int = 40_000
    customers: int = 60_000
    merchants: int = 5_000
    initial_date: date = date(2026, 9, 20)  # day of the initial customer load (ADR-008)
    channel_from: date = date(2026, 9, 28)  # the deliberate schema change: a new field appears
    daily_change_rate: float = 0.005
    fraud_rate: float = 0.003
    decline_rate: float = 0.03
    duplicate_rate: float = 0.01
    null_rate: float = 0.005
    bad_amount_rate: float = 0.003
    unknown_type_rate: float = 0.002
    bad_timestamp_rate: float = 0.001


@dataclass(frozen=True)
class ProfileEvent:
    customer_id: str
    entity_type: str
    segment: str
    region: str
    changed_at: datetime


def customer_id(n: int) -> str:
    # "CG" and "MG" can never collide with PaySim's C... and M... IDs, yet the first letter
    # still tells a customer from a merchant.
    return f"CG{n:07d}"


def merchant_id(n: int) -> str:
    return f"MG{n:06d}"


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _pick_customer(rng: random.Random, cfg: GeneratorConfig) -> str:
    # The power transform skews picks towards low IDs, so some customers are naturally busier.
    return customer_id(int(cfg.customers * rng.random() ** 1.3) + 1)


def _mule(rng: random.Random, cfg: GeneratorConfig) -> str:
    return customer_id(cfg.customers - rng.randrange(MULE_COUNT))


# --- transactions -------------------------------------------------------------------------


def _outcome(rng: random.Random, cfg: GeneratorConfig) -> tuple[str, str | None]:
    if rng.random() < cfg.decline_rate:
        return "DECLINED", rng.choice(DECLINE_REASONS)
    return "APPROVED", None


def _entry(ts, txn_type, amount, customer, counterparty, before, status, reason, fraud):
    """One transaction before ids, timestamp text and defects are applied."""
    if status == "DECLINED":
        after = before  # no money moved
    elif txn_type == "CASH_IN":
        after = before + amount
    else:
        after = before - amount
    row = {
        "type": txn_type,
        "amount": round(amount, 2),
        "customer_id": customer,
        "counterparty_id": counterparty,
        "origin_balance_before": round(before, 2),
        "origin_balance_after": round(after, 2),
        "status": status,
        "decline_reason": reason,
        "is_fraud": fraud,
        "source_system": "generator-v1",
    }
    return ts, row


def _normal_row(rng: random.Random, cfg: GeneratorConfig, day: date):
    txn_type = rng.choices(TYPES, cum_weights=TYPE_CUM)[0]
    ts = _midnight(day) + timedelta(
        hours=rng.choices(range(24), cum_weights=HOUR_CUM)[0],
        minutes=rng.randrange(60),
        seconds=rng.randrange(60),
    )
    customer = _pick_customer(rng, cfg)
    if txn_type in ("PAYMENT", "DEBIT"):
        counterparty = merchant_id(rng.randint(1, cfg.merchants))
    else:
        counterparty = _pick_customer(rng, cfg)
    mu, sigma = AMOUNT_SHAPE[txn_type]
    amount = max(round(rng.lognormvariate(mu, sigma), 2), 0.01)
    if txn_type == "CASH_IN":
        before = rng.lognormvariate(5.0, 1.2)
    else:
        # Always strictly more than the amount, so only fraud patterns empty an account.
        before = round(amount * (1 + rng.lognormvariate(0.5, 1.0)), 2)
        if before == amount:
            before = round(before + 0.01, 2)
    status, reason = _outcome(rng, cfg)
    return _entry(ts, txn_type, amount, customer, counterparty, before, status, reason, 0)


def _fraud_outcome(rng: random.Random) -> tuple[str, str | None]:
    # Fraud is only caught about 40% of the time, so some fraud is approved (a missed case).
    if rng.random() < 0.4:
        return "DECLINED", "SUSPECTED_FRAUD"
    return "APPROVED", None


def _drain_row(rng: random.Random, cfg: GeneratorConfig, day: date):
    """Fraud pattern 1: a transfer or cash-out that empties the whole origin balance."""
    txn_type = rng.choice(("TRANSFER", "CASH_OUT"))
    ts = _midnight(day) + timedelta(seconds=rng.randrange(86_400))
    balance = round(rng.lognormvariate(8.0, 0.8), 2)
    status, reason = _fraud_outcome(rng)
    return _entry(
        ts, txn_type, balance, _pick_customer(rng, cfg), _mule(rng, cfg), balance, status, reason, 1
    )


def _night_row(rng: random.Random, cfg: GeneratorConfig, day: date):
    """Fraud pattern 2: a high-value transfer in the quiet hours between 01:00 and 04:59."""
    ts = _midnight(day) + timedelta(
        hours=rng.randint(1, 4), minutes=rng.randrange(60), seconds=rng.randrange(60)
    )
    amount = round(rng.lognormvariate(7.5, 0.6), 2)
    before = round(amount * (1 + rng.lognormvariate(0.5, 1.0)), 2)
    status, reason = _fraud_outcome(rng)
    return _entry(
        ts, "TRANSFER", amount, _pick_customer(rng, cfg), _mule(rng, cfg), before, status, reason, 1
    )


def _burst_rows(rng: random.Random, cfg: GeneratorConfig, day: date):
    """Fraud pattern 3: one customer sends several transfers within about ten minutes."""
    customer = _pick_customer(rng, cfg)
    start = _midnight(day) + timedelta(hours=rng.randint(9, 21), minutes=rng.randrange(45))
    rows = []
    for k in range(BURST_SIZE):
        amount = round(rng.uniform(200, 900), 2)
        before = round(amount * (1 + rng.lognormvariate(0.5, 1.0)), 2)
        status, reason = _fraud_outcome(rng)
        ts = start + timedelta(minutes=2 * k + rng.randrange(2), seconds=rng.randrange(60))
        rows.append(
            _entry(ts, "TRANSFER", amount, customer, _mule(rng, cfg), before, status, reason, 1)
        )
    return rows


def transactions_for_day(cfg: GeneratorConfig, day: date) -> tuple[list[dict], dict]:
    """Return the day's rows (defects included) and a manifest of exactly what was injected."""
    rng = random.Random(f"{cfg.seed}:transactions:{day.isoformat()}")

    n_fraud = round(cfg.rows_per_day * cfg.fraud_rate)
    n_drain, n_night = round(n_fraud * 0.6), round(n_fraud * 0.2)
    n_bursts = round(n_fraud * 0.2 / BURST_SIZE)
    n_fraud_rows = n_drain + n_night + n_bursts * BURST_SIZE

    entries = [_normal_row(rng, cfg, day) for _ in range(cfg.rows_per_day - n_fraud_rows)]
    entries += [_drain_row(rng, cfg, day) for _ in range(n_drain)]
    entries += [_night_row(rng, cfg, day) for _ in range(n_night)]
    for _ in range(n_bursts):
        entries += _burst_rows(rng, cfg, day)
    entries.sort(key=lambda entry: entry[0])

    with_channel = day >= cfg.channel_from
    rows = []
    for i, (ts, body) in enumerate(entries, start=1):
        row = {"event_id": f"T{day:%Y%m%d}{i:06d}", "event_ts": ts.strftime(TS_FORMAT), **body}
        if with_channel:
            row["channel"] = rng.choice(CHANNELS)
        rows.append(row)
    declined = sum(row["status"] == "DECLINED" for row in rows)

    # Defects go on disjoint sets of rows, so each class count in the manifest is exact.
    classes = (
        ("null_field", cfg.null_rate),
        ("bad_amount", cfg.bad_amount_rate),
        ("unknown_type", cfg.unknown_type_rate),
        ("bad_timestamp", cfg.bad_timestamp_rate),
    )
    counts = {name: round(cfg.rows_per_day * rate) for name, rate in classes}
    picked = rng.sample(range(len(rows)), sum(counts.values()))
    start = 0
    for name, _ in classes:
        for index in picked[start : start + counts[name]]:
            row = rows[index]
            if name == "null_field":
                row[rng.choice(REQUIRED_FIELDS)] = None
            elif name == "bad_amount":
                row["amount"] = -abs(row["amount"])
            elif name == "unknown_type":
                row["type"] = rng.choice(BAD_TYPES)
            else:
                row["event_ts"] = rng.choice(BAD_TIMESTAMPS)
        start += counts[name]

    # Duplicates are exact copies of clean rows, like an upstream retry.
    defective = set(picked)
    pool = [i for i in range(len(rows)) if i not in defective]
    duplicates = rng.sample(pool, round(cfg.rows_per_day * cfg.duplicate_rate))
    rows.extend(dict(rows[i]) for i in duplicates)

    manifest = {
        "date": day.isoformat(),
        "seed": cfg.seed,
        "clean_rows": cfg.rows_per_day,
        "rows_written": len(rows),
        "fraud_rows": n_fraud_rows,
        "declined_rows": declined,
        "has_channel": with_channel,
        "defects": {**counts, "duplicates": len(duplicates)},
    }
    return rows, manifest


# --- customer profile (the SCD2 source) ----------------------------------------------------


def _initial_state(cfg: GeneratorConfig) -> dict[str, tuple[str, str, str]]:
    """Entity id -> (entity_type, segment, region) on the initial date."""
    rng = random.Random(f"{cfg.seed}:population")
    state = {}
    for n in range(1, cfg.customers + 1):
        segment = rng.choices(CUSTOMER_SEGMENTS, weights=CUSTOMER_SEGMENT_WEIGHTS)[0]
        state[customer_id(n)] = ("customer", segment, rng.choice(REGIONS))
    for n in range(1, cfg.merchants + 1):
        state[merchant_id(n)] = ("merchant", rng.choice(MERCHANT_SEGMENTS), rng.choice(REGIONS))
    return state


def _apply_changes(cfg: GeneratorConfig, state: dict, day: date) -> list[ProfileEvent]:
    """Change one attribute of a random slice of entities. Mutates `state`, returns the events."""
    rng = random.Random(f"{cfg.seed}:changes:{day.isoformat()}")
    events = []
    for entity in rng.sample(list(state), round(len(state) * cfg.daily_change_rate)):
        entity_type, segment, region = state[entity]
        if rng.random() < 0.5:
            options = CUSTOMER_SEGMENTS if entity_type == "customer" else MERCHANT_SEGMENTS
            segment = rng.choice([s for s in options if s != segment])
        else:
            region = rng.choice([r for r in REGIONS if r != region])
        state[entity] = (entity_type, segment, region)
        changed_at = _midnight(day) + timedelta(seconds=rng.randrange(86_400))
        events.append(ProfileEvent(entity, entity_type, segment, region, changed_at))
    events.sort(key=lambda event: event.changed_at)
    return events


def entity_state(cfg: GeneratorConfig, day: date) -> dict[str, tuple[str, str, str]]:
    """Attributes of every entity after all changes up to and including `day`.

    Replaying from the initial date keeps each day independent of files on disk: any single
    day can be regenerated on its own and still be consistent with the others.
    """
    if day < cfg.initial_date:
        raise ValueError(f"{day} is before the initial load on {cfg.initial_date}")
    state = _initial_state(cfg)
    current = cfg.initial_date
    while current < day:
        current += timedelta(days=1)
        _apply_changes(cfg, state, current)
    return state


def profile_events_for_day(cfg: GeneratorConfig, day: date) -> list[ProfileEvent]:
    if day == cfg.initial_date:
        initial = _midnight(day)
        return [
            ProfileEvent(entity, etype, segment, region, initial)
            for entity, (etype, segment, region) in _initial_state(cfg).items()
        ]
    return _apply_changes(cfg, entity_state(cfg, day - timedelta(days=1)), day)


# --- writing files -----------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temp name, then rename. A half-written file must never be picked up."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    os.replace(temp, path)


def _profile_csv(events: list[ProfileEvent]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PROFILE_COLUMNS)
    for e in events:
        writer.writerow(
            (e.customer_id, e.entity_type, e.segment, e.region, e.changed_at.strftime(TS_FORMAT))
        )
    return buffer.getvalue()


def write_day(cfg: GeneratorConfig, day: date, out_dir: Path) -> dict:
    """Write the three files for `day` and return its manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, manifest = transactions_for_day(cfg, day)
    events = profile_events_for_day(cfg, day)
    manifest["profile_rows"] = len(events)

    lines = (json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    _atomic_write(out_dir / f"transactions_{day.isoformat()}.jsonl", "".join(lines))
    _atomic_write(out_dir / f"customer_profile_{day.isoformat()}.csv", _profile_csv(events))
    _atomic_write(
        out_dir / f"manifest_{day.isoformat()}.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the daily feed files.")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--seed", type=int, default=GeneratorConfig.seed)
    args = parser.parse_args(argv)

    cfg = GeneratorConfig(seed=args.seed)
    day = args.start
    while day <= args.end:
        manifest = write_day(cfg, day, args.out)
        print(
            f"{day}: {manifest['rows_written']:,} rows, {manifest['profile_rows']:,} profile rows,"
            f" defects {manifest['defects']}"
        )
        day += timedelta(days=1)


if __name__ == "__main__":
    main()
