"""The app database loader: incremental, idempotent, and safe to fail."""

from datetime import timedelta

import pytest

from ingestion import app_db_incremental, meta
from tests.conftest import UNTIL, raw_count


def load(src, dwh):
    with meta.track_run(dwh, "app_db") as run:
        app_db_incremental.load(src, dwh, run)
    return run


def source_count(src, table):
    return src.execute(f"select count(*) from {table}").fetchone()[0]


def test_first_load_copies_every_table(src, dwh, simulate):
    simulate()
    load(src, dwh)
    for table in app_db_incremental.TABLES:
        assert raw_count(dwh, f"app_{table}") == source_count(src, table) > 0


def test_running_twice_loads_no_duplicates(src, dwh, simulate):
    simulate()
    load(src, dwh)
    before = {t: raw_count(dwh, f"app_{t}") for t in app_db_incremental.TABLES}

    second = load(src, dwh)

    assert second.rows_loaded == 0
    assert {t: raw_count(dwh, f"app_{t}") for t in app_db_incremental.TABLES} == before


def test_changed_row_is_updated_not_duplicated(src, dwh, simulate):
    simulate()
    load(src, dwh)
    src.execute(
        "update customers set loyalty_tier = 'gold', updated_at = %s where customer_id = 1",
        (UNTIL + timedelta(hours=1),),
    )

    run = load(src, dwh)

    assert run.details["customers"]["updated"] == 1
    assert run.details["customers"]["inserted"] == 0
    assert raw_count(dwh, "app_customers") == source_count(src, "customers")
    tier = dwh.execute("select loyalty_tier from raw.app_customers where customer_id = 1").fetchone()[0]
    assert tier == "gold"


def test_only_new_activity_is_extracted(src, dwh, simulate):
    simulate()
    load(src, dwh)
    simulate(until=UNTIL + timedelta(days=1))

    run = load(src, dwh)

    bookings = run.details["bookings"]
    assert bookings["inserted"] > 0
    # Far fewer rows were read than the table holds: only the last day's changes.
    assert bookings["extracted"] < source_count(src, "bookings") / 2
    assert raw_count(dwh, "app_bookings") == source_count(src, "bookings")


def test_new_source_column_is_added_to_raw(src, dwh, simulate):
    simulate()
    load(src, dwh)
    src.execute("alter table customers add column preferred_language text")
    src.execute(
        "update customers set preferred_language = 'de', updated_at = %s where customer_id = 2",
        (UNTIL + timedelta(hours=1),),
    )

    load(src, dwh)

    language = dwh.execute("select preferred_language from raw.app_customers where customer_id = 2").fetchone()[0]
    assert language == "de"


def test_failed_load_changes_nothing(src, dwh, simulate, monkeypatch):
    simulate()

    def broken(*args, **kwargs):
        raise ConnectionError("warehouse went away")

    monkeypatch.setattr(meta, "set_watermark", broken)
    with pytest.raises(ConnectionError):
        load(src, dwh)

    # The table, the upsert and the watermark share one transaction, so all rolled back.
    assert dwh.execute("select to_regclass('raw.app_customers')").fetchone()[0] is None
    assert meta.get_watermark(dwh, "app_db", "customers") is None
    status, error = dwh.execute("select status, error from meta.load_runs order by run_id desc limit 1").fetchone()
    assert status == "failed" and "warehouse went away" in error

    monkeypatch.undo()
    load(src, dwh)  # the next run just does the work
    assert raw_count(dwh, "app_customers") == source_count(src, "customers")
