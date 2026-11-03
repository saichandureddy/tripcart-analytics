"""Website click events: build them, hold back the late ones, upload as files.

Each event is one JSON object. Events are written as JSON Lines files (one
event per line) into MinIO, partitioned by the hour they *arrived*:

    clickstream/date=2026-11-02/hour=14/events-20261102T150000-1a2b3c4d.jsonl

An event can arrive hours after it happened (a phone that was offline), so a
file for 14:00 can contain events from 11:00. That is the "late-arriving data"
the dbt layer has to handle.
"""

import json
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

PREFIX = "clickstream/"

DEVICES = (["desktop", "mobile", "tablet"], [50, 42, 8])
UTM_SOURCES = (["direct", "google", "newsletter", "social", "partner"], [35, 30, 15, 12, 8])


def iso(ts: datetime) -> str:
    """2026-11-02T14:03:22.123Z, the way most trackers send timestamps."""
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Session:
    """One visit to the website. Call add() for each step, oldest first."""

    def __init__(
        self,
        rng: random.Random,
        start: datetime,
        customer_id: int | None,
        anonymous_id: str,
        channel: str,
        schema_drift: bool,
    ):
        self.rng = rng
        self.ts = start
        self.customer_id = customer_id
        self.anonymous_id = anonymous_id
        self.session_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        self.device = "app" if channel == "mobile_app" else rng.choices(*DEVICES)[0]
        self.utm_source = "partner" if channel == "partner" else rng.choices(*UTM_SOURCES)[0]
        self.schema_drift = schema_drift
        self.events: list[dict] = []

    def add(self, event_type: str, page_url: str, **properties) -> None:
        # The new app version renamed `currency` to `currency_code`.
        if self.schema_drift and "currency" in properties:
            properties["currency_code"] = properties.pop("currency")
        self.events.append(
            {
                "event_id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
                "event_type": event_type,
                "event_ts": iso(self.ts),
                "session_id": self.session_id,
                "anonymous_id": self.anonymous_id,
                "customer_id": self.customer_id,
                "device": self.device,
                "utm_source": self.utm_source,
                "page_url": page_url,
                "app_version": "2.0.0" if self.schema_drift else "1.8.3",
                "properties": properties,
            }
        )
        self.ts += timedelta(seconds=self.rng.randint(8, 150))

    def end_at(self, ts: datetime) -> None:
        """Move the whole session so its last event (the purchase) happens at `ts`."""
        shift = ts - datetime.fromisoformat(self.events[-1]["event_ts"])
        for event in self.events:
            happened = datetime.fromisoformat(event["event_ts"])
            event["event_ts"] = iso(happened + shift)


def schedule_arrivals(
    rng: random.Random, events: list[dict], late_rate: float, duplicate_rate: float
) -> list[tuple[datetime, dict]]:
    """Decide when each event reaches the server. Some arrive late, some twice."""
    out = []
    for event in events:
        happened = datetime.fromisoformat(event["event_ts"])
        if rng.random() < late_rate:
            arrival = happened + timedelta(hours=rng.uniform(1, 48))
        else:
            arrival = happened + timedelta(seconds=rng.uniform(0.2, 20))
        out.append((arrival, event))
        if rng.random() < duplicate_rate:
            # The client retried: the same event, with the same event_id, again.
            out.append((arrival + timedelta(seconds=rng.uniform(1, 600)), event))
    return out


def upload_files(s3, bucket: str, arrived: list[tuple[datetime, dict]], written_at: datetime) -> int:
    """Group events by arrival hour and upload one JSON Lines file per hour."""
    by_hour = defaultdict(list)
    for arrival, event in arrived:
        by_hour[arrival.replace(minute=0, second=0, microsecond=0)].append((arrival, event))

    for hour, rows in sorted(by_hour.items()):
        rows.sort(key=lambda r: r[0])
        body = "".join(json.dumps(event, separators=(",", ":")) + "\n" for _, event in rows)
        key = (
            f"{PREFIX}date={hour:%Y-%m-%d}/hour={hour:%H}/"
            f"events-{written_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl"
        )
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType="application/x-ndjson")
    return len(by_hour)
