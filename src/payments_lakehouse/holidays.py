"""Fetch the GOV.UK bank holidays calendar and land it, untouched, in the landing zone.

The raw payload is saved exactly as received, under a name derived from a hash of its content.
Pulling an unchanged calendar rewrites nothing (idempotent), and a changed calendar appears as a
new file for Auto Loader to ingest. Parsing is deliberately left to silver, so a change in the
API's shape cannot break ingestion. See docs/decisions.md (ADR-009).
"""

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://www.gov.uk/bank-holidays.json"
DIVISIONS = ("england-and-wales", "scotland", "northern-ireland")
EVENT_FIELDS = ("title", "date", "notes", "bunting")


def fetch(
    url=URL, *, timeout=20, attempts=3, backoff=2.0, opener=urllib.request.urlopen, sleep=time.sleep
) -> bytes:
    """GET the payload, retrying server errors and network failures but not client errors.

    The opener and sleep are parameters so tests run without a network or real waiting.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(url, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code < 500:  # a 4xx will not fix itself, so fail straight away
                raise
            last_error = error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
        if attempt < attempts:
            sleep(backoff * attempt)
    raise last_error


def validate(payload: bytes) -> dict:
    """Check the payload has the shape silver expects: the API's data contract."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"payload is not valid JSON: {error}") from error
    missing = [division for division in DIVISIONS if division not in data]
    if missing:
        raise ValueError(f"missing divisions: {missing}")
    for division in DIVISIONS:
        events = data[division].get("events")
        if not isinstance(events, list) or not events:
            raise ValueError(f"{division} has no events")
        for event in events:
            absent = [field for field in EVENT_FIELDS if field not in event]
            if absent:
                raise ValueError(f"{division} event {event!r} is missing {absent}")
            date.fromisoformat(event["date"])  # raises ValueError on a bad date
    return data


def landing_name(payload: bytes) -> str:
    return f"bank_holidays_{hashlib.sha256(payload).hexdigest()[:12]}.json"


def pull(dest_dir: Path, **fetch_options) -> tuple[Path, bool]:
    """Fetch, validate and land the calendar. Returns the file and whether it was newly written."""
    payload = fetch(**fetch_options)
    validate(payload)  # never land a payload that silver could not read
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / landing_name(payload)
    if path.exists():
        return path, False
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)  # rename, so a half-written file is never picked up
    return path, True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Land the GOV.UK bank holidays calendar.")
    parser.add_argument("--dest", type=Path, required=True, help="landing directory")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args(argv)

    path, created = pull(args.dest, url=args.url)
    print(f"{'wrote' if created else 'unchanged'}: {path}")


if __name__ == "__main__":
    main()
