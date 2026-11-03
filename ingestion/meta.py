"""Bookkeeping tables the loaders keep in the warehouse's `meta` schema.

    meta.load_runs     one row per loader run: when, how many rows, success or error
    meta.watermarks    how far each incremental table has been loaded
    meta.loaded_files  every clickstream file already loaded, so it is never loaded twice

The data itself goes to the `raw` schema. Keeping the bookkeeping apart means
`raw` holds only what came from the sources.
"""

import json
import logging
import traceback
from contextlib import contextmanager
from datetime import datetime

import psycopg

log = logging.getLogger("ingestion")

META_DDL = """
create schema if not exists raw;
create schema if not exists meta;

create table if not exists meta.load_runs (
    run_id      bigserial primary key,
    source      text not null,
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    status      text not null default 'running',   -- running, succeeded, failed
    rows_loaded bigint,
    details     jsonb,
    error       text
);

create table if not exists meta.watermarks (
    source     text not null,
    object     text not null,
    watermark  timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (source, object)
);

create table if not exists meta.loaded_files (
    file_key    text primary key,
    etag        text,
    size_bytes  bigint,
    rows_loaded int not null,
    run_id      bigint not null,
    loaded_at   timestamptz not null default now()
);
"""


def ensure_meta(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(META_DDL)


class Run:
    def __init__(self, run_id: int):
        self.run_id = run_id
        self.rows_loaded = 0
        self.details: dict = {}


@contextmanager
def track_run(conn: psycopg.Connection, source: str):
    """Record a loader run in meta.load_runs, including the error if it fails.

    The run row is written in its own small transactions, so a failed load
    still leaves a 'failed' row behind to look at.
    """
    ensure_meta(conn)
    run_id = conn.execute("insert into meta.load_runs (source) values (%s) returning run_id", (source,)).fetchone()[0]
    run = Run(run_id)
    log.info("%s: run %s started", source, run_id)
    try:
        yield run
    except Exception as exc:
        conn.execute(
            "update meta.load_runs set status = 'failed', finished_at = now(), error = %s where run_id = %s",
            (f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}", run_id),
        )
        log.error("%s: run %s failed: %s", source, run_id, exc)
        raise
    conn.execute(
        "update meta.load_runs set status = 'succeeded', finished_at = now(), rows_loaded = %s, details = %s "
        "where run_id = %s",
        (run.rows_loaded, json.dumps(run.details, default=str), run_id),
    )
    log.info("%s: run %s succeeded, %s rows loaded", source, run_id, run.rows_loaded)


def get_watermark(conn: psycopg.Connection, source: str, obj: str) -> datetime | None:
    row = conn.execute(
        "select watermark from meta.watermarks where source = %s and object = %s", (source, obj)
    ).fetchone()
    return row[0] if row else None


def set_watermark(conn: psycopg.Connection, source: str, obj: str, watermark: datetime) -> None:
    conn.execute(
        "insert into meta.watermarks (source, object, watermark) values (%s, %s, %s) "
        "on conflict (source, object) do update set watermark = excluded.watermark, updated_at = now()",
        (source, obj, watermark),
    )
