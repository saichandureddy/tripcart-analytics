"""API ingestion: daily exchange rates from Frankfurter -> raw.fx_rates.

Frankfurter (https://frankfurter.dev) is a free, keyless API serving central
bank reference rates, such as the European Central Bank's. Rates are
published on working days only, so weekends and holidays have no rows; dbt
fills those gaps.

We call its v2 endpoint, which returns one JSON object per rate:

    GET /v2/rates?from=2026-06-01&to=2026-06-30&base=EUR&quotes=USD,GBP,INR
    [{"date": "2026-06-01", "base": "EUR", "quote": "USD", "rate": 1.13}, ...]

Each run asks for the days from a week before the newest rate already
loaded up to today, so a missed day is picked up automatically. Rates are
upserted on (date, base, quote), so overlapping runs never duplicate.

Networks fail, so every request has a timeout and is retried with
exponential backoff (1s, 2s, 4s, ...) on timeouts, connection errors and
"try again later" responses (429 and 5xx). Other errors, like a 404 from a
wrong URL, fail at once: retrying would not fix them.
"""

import logging
import os
import random
import time
from datetime import UTC, date, datetime, timedelta

import psycopg
import requests

from ingestion import meta, settings

log = logging.getLogger("ingestion")

SOURCE = "fx_rates"
BASE = "EUR"
QUOTES = ["USD", "GBP", "INR"]
LOOKBACK_DAYS = 7
# Requests cover at most this many days, to keep each response small.
CHUNK_DAYS = 60
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

DDL = """
create table if not exists raw.fx_rates (
    rate_date      date not null,
    base_currency  char(3) not null,
    quote_currency char(3) not null,
    rate           numeric(18, 8) not null,
    _loaded_at     timestamptz not null,
    _run_id        bigint not null,
    primary key (rate_date, base_currency, quote_currency)
);
"""


class RetryableHTTPError(Exception):
    pass


def get_json(
    session: requests.Session,
    url: str,
    params: dict,
    *,
    max_attempts: int = 5,
    timeout: float = 10,
    backoff: float = 1.0,
    sleep=time.sleep,
) -> dict:
    """GET a URL and return its JSON, retrying the failures that are worth retrying."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in RETRYABLE_STATUS:
                raise RetryableHTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, RetryableHTTPError) as exc:
            if attempt == max_attempts:
                raise RuntimeError(f"FX API still failing after {max_attempts} attempts: {exc}") from exc
            wait = backoff * 2 ** (attempt - 1) + random.uniform(0, 0.5)
            log.warning("fx_rates: attempt %d failed (%s), retrying in %.1fs", attempt, exc, wait)
            sleep(wait)


def date_chunks(start: date, end: date, days: int = CHUNK_DAYS):
    while start <= end:
        chunk_end = min(start + timedelta(days=days - 1), end)
        yield start, chunk_end
        start = chunk_end + timedelta(days=1)


def default_start(dwh: psycopg.Connection, today: date) -> date:
    newest = dwh.execute("select max(rate_date) from raw.fx_rates").fetchone()[0]
    if newest:
        return newest - timedelta(days=LOOKBACK_DAYS)
    # First run: go back far enough to cover the simulator's history.
    return today - timedelta(days=int(os.environ.get("TRIPCART_FX_BACKFILL_DAYS", "120")))


def load(
    dwh: psycopg.Connection,
    run: meta.Run,
    *,
    session: requests.Session | None = None,
    start: date | None = None,
    end: date | None = None,
    sleep=time.sleep,
) -> None:
    session = session or requests.Session()
    with dwh.transaction():
        dwh.execute(DDL)
    end = end or datetime.now(UTC).date()
    start = start or default_start(dwh, end)

    rows = []
    for chunk_start, chunk_end in date_chunks(start, end):
        params = {"from": str(chunk_start), "to": str(chunk_end), "base": BASE, "quotes": ",".join(QUOTES)}
        data = get_json(session, f"{settings.fx_api_url()}/rates", params, sleep=sleep)
        for item in data:
            if item["quote"] != item["base"]:  # skip the EUR -> EUR = 1 identity row
                rows.append((date.fromisoformat(item["date"]), item["base"], item["quote"], item["rate"]))
        log.info("fx_rates: fetched %s to %s", chunk_start, chunk_end)

    with dwh.transaction(), dwh.cursor() as cur:
        changed = 0
        for row in rows:
            cur.execute(
                """
                insert into raw.fx_rates (rate_date, base_currency, quote_currency, rate, _loaded_at, _run_id)
                values (%s, %s, %s, %s, now(), %s)
                on conflict (rate_date, base_currency, quote_currency) do update
                    set rate = excluded.rate, _loaded_at = excluded._loaded_at, _run_id = excluded._run_id
                    where raw.fx_rates.rate is distinct from excluded.rate
                """,
                (*row, run.run_id),
            )
            changed += cur.rowcount
    run.rows_loaded = changed
    run.details = {"from": str(start), "to": str(end), "rates_received": len(rows), "rows_changed": changed}
