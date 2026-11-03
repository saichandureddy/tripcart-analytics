"""The simulator: produces related, deliberately messy data, and resumes cleanly."""

from datetime import timedelta

from tests.conftest import UNTIL


def test_creates_related_rows(src, simulate):
    simulate()
    orphans = src.execute("""
        select count(*) from payments p
        where not exists (select 1 from bookings b where b.booking_id = p.booking_id)
          and not exists (select 1 from shop_orders o where o.order_id = p.order_id)
    """).fetchone()[0]
    assert orphans == 0
    assert src.execute("select count(*) from bookings").fetchone()[0] > 0
    assert src.execute("select count(distinct status) from shop_orders").fetchone()[0] >= 3


def test_second_run_continues_where_the_first_stopped(src, simulate):
    simulate()
    newest = src.execute("select max(updated_at) from bookings").fetchone()[0]
    assert newest <= UNTIL

    summary = simulate(until=UNTIL)  # same "now": nothing new to simulate
    assert summary["from"] == summary["to"] == UNTIL

    simulate(until=UNTIL + timedelta(days=1))
    assert src.execute("select min(created_at) from bookings where created_at > %s", (UNTIL,)).fetchone()[0]


def test_writes_clickstream_files_by_arrival_hour(s3, simulate):
    simulate()
    keys = list(s3.objects)
    assert keys and all(k.startswith("clickstream/date=") and "/hour=" in k for k in keys)
