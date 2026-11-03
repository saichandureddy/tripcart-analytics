"""The FX loader: survives a flaky API, and reruns don't duplicate."""

from datetime import date

import pytest
import requests

from ingestion import fx_rates_api, meta
from tests.conftest import raw_count

# What /v2/rates returns: one object per day and currency, plus the identity row.
RATES = [
    {"date": day, "base": "EUR", "quote": quote, "rate": rate}
    for day in ["2026-06-01", "2026-06-02", "2026-06-03"]
    for quote, rate in [("EUR", 1.0), ("GBP", 0.85), ("INR", 92.1), ("USD", 1.13)]
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Plays back a script: each item is a response to return or an exception to raise."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step


def load(dwh, session, sleeps):
    with meta.track_run(dwh, "fx_rates") as run:
        fx_rates_api.load(dwh, run, session=session, start=date(2026, 6, 1), end=date(2026, 6, 3), sleep=sleeps.append)
    return run


def test_loads_rates(dwh):
    load(dwh, FakeSession(FakeResponse(200, RATES)), [])
    assert raw_count(dwh, "fx_rates") == 9


def test_survives_timeouts(dwh):
    sleeps = []
    session = FakeSession(
        requests.Timeout("slow"), requests.ConnectionError("reset"), FakeResponse(503), FakeResponse(200, RATES)
    )

    load(dwh, session, sleeps)

    assert len(session.calls) == 4
    assert raw_count(dwh, "fx_rates") == 9
    # Exponential backoff: each wait is about twice the one before.
    assert sleeps[0] < sleeps[1] < sleeps[2]


def test_gives_up_after_max_attempts_and_records_failure(dwh):
    session = FakeSession(requests.Timeout("down"))

    with pytest.raises(RuntimeError, match="after 5 attempts"):
        load(dwh, session, [])

    assert len(session.calls) == 5
    assert dwh.execute("select status from meta.load_runs order by run_id desc limit 1").fetchone()[0] == "failed"


def test_client_errors_are_not_retried(dwh):
    session = FakeSession(FakeResponse(404))
    with pytest.raises(requests.HTTPError):
        load(dwh, session, [])
    assert len(session.calls) == 1


def test_running_twice_loads_no_duplicates(dwh):
    load(dwh, FakeSession(FakeResponse(200, RATES)), [])
    second = load(dwh, FakeSession(FakeResponse(200, RATES)), [])

    assert raw_count(dwh, "fx_rates") == 9
    assert second.rows_loaded == 0


def test_long_ranges_are_split_into_chunks():
    chunks = list(fx_rates_api.date_chunks(date(2026, 1, 1), date(2026, 4, 30), days=60))
    assert chunks == [(date(2026, 1, 1), date(2026, 3, 1)), (date(2026, 3, 2), date(2026, 4, 30))]
