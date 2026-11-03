"""The clickstream loader: every file exactly once, bad lines set aside."""

import json

from ingestion import clickstream_files, meta
from tests.conftest import raw_count


def load(dwh, s3):
    with meta.track_run(dwh, "clickstream") as run:
        clickstream_files.load(dwh, s3, "test-bucket", run)
    return run


def put_file(s3, key, events):
    s3.put_object(Bucket="test-bucket", Key=key, Body="".join(json.dumps(e) + "\n" for e in events))


def test_loads_every_line_of_every_file(dwh, s3, simulate):
    simulate()
    lines = sum(body.count(b"\n") for body in s3.objects.values())

    load(dwh, s3)

    assert raw_count(dwh, "clickstream_events") == lines > 0


def test_running_twice_loads_no_duplicates(dwh, s3, simulate):
    simulate()
    load(dwh, s3)
    before = raw_count(dwh, "clickstream_events")

    second = load(dwh, s3)

    assert second.rows_loaded == 0
    assert raw_count(dwh, "clickstream_events") == before


def test_only_new_files_are_loaded(dwh, s3):
    put_file(s3, "clickstream/date=2026-06-01/hour=10/a.jsonl", [{"event_id": "1"}, {"event_id": "2"}])
    load(dwh, s3)
    put_file(s3, "clickstream/date=2026-06-01/hour=11/b.jsonl", [{"event_id": "3"}])

    run = load(dwh, s3)

    assert run.details["files"] == 1
    assert raw_count(dwh, "clickstream_events") == 3


def test_bad_line_is_rejected_not_fatal(dwh, s3):
    key = "clickstream/date=2026-06-01/hour=10/broken.jsonl"
    s3.put_object(Bucket="test-bucket", Key=key, Body='{"event_id": "1"}\n{"event_id": \n{"event_id": "3"}\n')

    run = load(dwh, s3)

    assert run.rows_loaded == 2
    assert dwh.execute("select line_no from meta.rejected_lines where file_key = %s", (key,)).fetchall() == [(2,)]


def test_source_mess_is_kept_for_dbt(dwh, s3, simulate):
    """raw is untouched: duplicates and both currency field names are still there."""
    simulate()
    load(dwh, s3)
    duplicates, old_name, new_name = dwh.execute("""
        select count(*) - count(distinct event->>'event_id'),
               count(*) filter (where event->'properties' ? 'currency'),
               count(*) filter (where event->'properties' ? 'currency_code')
        from raw.clickstream_events
    """).fetchone()
    assert duplicates > 0 and old_name > 0 and new_name > 0
