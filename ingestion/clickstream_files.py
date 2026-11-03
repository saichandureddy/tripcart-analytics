"""File ingestion: clickstream JSON Lines files in MinIO -> raw.clickstream_events.

Each run lists the bucket, skips every file already recorded in
meta.loaded_files, and loads the rest. Loading a file and recording it happen
in one transaction, so a file is loaded completely or not at all, and never
twice.

Each line is stored as it came, in a jsonb column, together with the file it
came from and its line number. Nothing is cleaned here: duplicate events,
late events and the renamed `currency_code` field all land in raw, and the
dbt staging model deals with them. A line that is not valid JSON goes to
meta.rejected_lines instead of stopping the load.
"""

import json
import logging

import psycopg
from psycopg.types.json import Jsonb

from ingestion import meta

log = logging.getLogger("ingestion")

SOURCE = "clickstream"
PREFIX = "clickstream/"

DDL = """
create table if not exists raw.clickstream_events (
    file_key       text not null,
    line_no        int not null,
    event          jsonb not null,
    file_modified_at timestamptz,
    _loaded_at     timestamptz not null,
    _run_id        bigint not null,
    primary key (file_key, line_no)
);

create table if not exists meta.rejected_lines (
    file_key   text not null,
    line_no    int not null,
    raw_line   text not null,
    error      text not null,
    run_id     bigint not null,
    primary key (file_key, line_no)
);
"""


def list_new_files(dwh: psycopg.Connection, s3, bucket: str) -> list[dict]:
    """Every .jsonl file in the bucket that is not in meta.loaded_files yet, oldest key first."""
    loaded = {row[0] for row in dwh.execute("select file_key from meta.loaded_files")}
    new = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".jsonl") and obj["Key"] not in loaded:
                new.append(obj)
    return sorted(new, key=lambda o: o["Key"])


def load_file(dwh: psycopg.Connection, s3, bucket: str, obj: dict, run_id: int) -> tuple[int, int]:
    key = obj["Key"]
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    good, bad = [], []
    for line_no, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            good.append((key, line_no, Jsonb(json.loads(line)), obj.get("LastModified"), run_id))
        except json.JSONDecodeError as exc:
            bad.append((key, line_no, line, str(exc), run_id))

    with dwh.transaction():
        with dwh.cursor() as cur:
            cur.executemany(
                "insert into raw.clickstream_events "
                "(file_key, line_no, event, file_modified_at, _loaded_at, _run_id) "
                "values (%s, %s, %s, %s, now(), %s) on conflict do nothing",
                good,
            )
            cur.executemany("insert into meta.rejected_lines values (%s, %s, %s, %s, %s) on conflict do nothing", bad)
        dwh.execute(
            "insert into meta.loaded_files (file_key, etag, size_bytes, rows_loaded, run_id) values (%s, %s, %s, %s, %s)",
            (key, obj.get("ETag", "").strip('"'), obj.get("Size"), len(good), run_id),
        )
    if bad:
        log.warning("clickstream: %s had %d unreadable lines, see meta.rejected_lines", key, len(bad))
    return len(good), len(bad)


def load(dwh: psycopg.Connection, s3, bucket: str, run: meta.Run) -> None:
    with dwh.transaction():
        dwh.execute(DDL)
    files = list_new_files(dwh, s3, bucket)
    log.info("clickstream: %d new files in s3://%s/%s", len(files), bucket, PREFIX)
    rejected = 0
    for i, obj in enumerate(files, start=1):
        rows, bad = load_file(dwh, s3, bucket, obj, run.run_id)
        run.rows_loaded += rows
        rejected += bad
        if i % 200 == 0:
            log.info("clickstream: %d of %d files loaded", i, len(files))
    run.details = {"files": len(files), "rows": run.rows_loaded, "rejected_lines": rejected}
