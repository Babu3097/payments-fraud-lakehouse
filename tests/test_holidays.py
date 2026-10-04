import json
import urllib.error

import pytest

from payments_lakehouse.holidays import fetch, landing_name, pull, validate


def calendar(**overrides):
    """A minimal valid payload in the shape the GOV.UK API returns."""
    event = {"title": "New Year's Day", "date": "2026-01-01", "notes": "", "bunting": True}
    data = {
        division: {"division": division, "events": [dict(event)]}
        for division in ("england-and-wales", "scotland", "northern-ireland")
    }
    data.update(overrides)
    return json.dumps(data).encode()


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(*outcomes):
    """Each call returns the next outcome: bytes to serve, or an exception to raise."""
    calls = []

    def opener(url, timeout):
        calls.append(url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "error", {}, None)


def no_sleep(_seconds):
    return None


def test_validate_accepts_a_well_formed_calendar():
    assert set(validate(calendar())) == {"england-and-wales", "scotland", "northern-ireland"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"<html>maintenance</html>", "not valid JSON"),
        (json.dumps({"scotland": {"events": []}}).encode(), "missing divisions"),
        (calendar(scotland={"events": []}), "scotland has no events"),
        (calendar(scotland={"events": [{"title": "x", "date": "2026-01-01"}]}), "missing"),
        (
            calendar(
                scotland={
                    "events": [{"title": "x", "date": "01/01/2026", "notes": "", "bunting": 1}]
                }
            ),
            "Invalid isoformat",
        ),
    ],
)
def test_validate_rejects_payloads_silver_could_not_read(payload, message):
    with pytest.raises(ValueError, match=message):
        validate(payload)


def test_landing_name_depends_only_on_content():
    assert landing_name(calendar()) == landing_name(calendar())
    assert landing_name(calendar()) != landing_name(calendar(extra={"events": []}))


def test_pull_lands_the_payload_byte_for_byte(tmp_path):
    payload = calendar()
    path, created = pull(tmp_path, opener=opener_returning(payload))
    assert created
    assert path.read_bytes() == payload
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no temp file left behind


def test_pulling_an_unchanged_calendar_writes_nothing(tmp_path):
    payload = calendar()
    first, _ = pull(tmp_path, opener=opener_returning(payload))
    modified = first.stat().st_mtime_ns
    second, created = pull(tmp_path, opener=opener_returning(payload))
    assert not created
    assert second == first
    assert second.stat().st_mtime_ns == modified


def test_a_changed_calendar_becomes_a_new_file(tmp_path):
    pull(tmp_path, opener=opener_returning(calendar()))
    changed = calendar(
        scotland={
            "division": "scotland",
            "events": [{"title": "Extra day", "date": "2026-06-01", "notes": "", "bunting": False}],
        }
    )
    _, created = pull(tmp_path, opener=opener_returning(changed))
    assert created
    assert len(list(tmp_path.iterdir())) == 2


def test_an_invalid_payload_is_never_landed(tmp_path):
    with pytest.raises(ValueError, match="not valid JSON"):
        pull(tmp_path, opener=opener_returning(b"not json"))
    assert not list(tmp_path.iterdir())


def test_fetch_retries_a_server_error_then_succeeds():
    opener = opener_returning(http_error(503), http_error(502), b"ok")
    assert fetch(opener=opener, sleep=no_sleep) == b"ok"
    assert len(opener.calls) == 3


def test_fetch_does_not_retry_a_client_error():
    opener = opener_returning(http_error(404), b"ok")
    with pytest.raises(urllib.error.HTTPError):
        fetch(opener=opener, sleep=no_sleep)
    assert len(opener.calls) == 1


def test_fetch_gives_up_after_the_last_attempt():
    opener = opener_returning(urllib.error.URLError("network down"))
    with pytest.raises(urllib.error.URLError, match="network down"):
        fetch(attempts=3, opener=opener, sleep=no_sleep)
    assert len(opener.calls) == 3
