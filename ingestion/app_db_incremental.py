"""Incremental extract-load: TripCart's app database -> warehouse raw schema.

For each source table:

1. Read the watermark: the newest `updated_at` loaded last time.
2. Extract only rows with `updated_at` at or after (watermark - lookback).
   The small lookback catches rows whose transaction committed a little
   after a later row's did, which a strict `>` would miss forever.
3. Upsert them into raw.app_<table> on the primary key. A row that is
   already there with the same `updated_at` is left alone, so the lookback
   overlap and reruns never create duplicates or rewrite unchanged rows.
4. Move the watermark forward, in the same transaction as the upsert. If
   the load fails halfway, both roll back together and the next run simply
   repeats it.

The raw table mirrors the source columns and types, plus `_loaded_at` and
`_run_id`. If the source table gains a column, the loader adds it to the raw
table (schema evolution). Hard deletes in the source are not detected; the
app uses status columns (cancelled, refunded) instead of deleting rows.
"""

import logging
from datetime import timedelta

import psycopg
from psycopg import sql

from ingestion import meta

log = logging.getLogger("ingestion")

SOURCE = "app_db"
SOURCE_SCHEMA = "public"
TARGET_SCHEMA = "raw"

# Source table -> its primary key columns.
TABLES = {
    "customers": ["customer_id"],
    "products": ["product_id"],
    "bookings": ["booking_id"],
    "shop_orders": ["order_id"],
    "order_items": ["order_id", "line_no"],
    "payments": ["payment_id"],
}
LOOKBACK = timedelta(minutes=10)
BATCH_SIZE = 5000


def source_columns(src: psycopg.Connection, table: str) -> list[tuple[str, str]]:
    """Column names and exact types of a source table, in table order."""
    rows = src.execute(
        """
        select a.attname, format_type(a.atttypid, a.atttypmod)
        from pg_attribute a
        join pg_class c on c.oid = a.attrelid
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = %s and c.relname = %s and a.attnum > 0 and not a.attisdropped
        order by a.attnum
        """,
        (SOURCE_SCHEMA, table),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"Source table {SOURCE_SCHEMA}.{table} not found. Has the simulator run yet?")
    return rows


def ensure_raw_table(
    dwh: psycopg.Connection, raw_table: str, columns: list[tuple[str, str]], primary_key: list[str]
) -> None:
    """Create raw.<raw_table> like the source, or add columns the source has gained."""
    target = sql.Identifier(TARGET_SCHEMA, raw_table)
    existing = {
        r[0]
        for r in dwh.execute(
            "select column_name from information_schema.columns where table_schema = %s and table_name = %s",
            (TARGET_SCHEMA, raw_table),
        )
    }
    if not existing:
        column_defs = [sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(type_)) for name, type_ in columns]
        dwh.execute(
            sql.SQL(
                "create table {} ({}, _loaded_at timestamptz not null, _run_id bigint not null, primary key ({}))"
            ).format(target, sql.SQL(", ").join(column_defs), sql.SQL(", ").join(map(sql.Identifier, primary_key)))
        )
        log.info("app_db: created %s.%s", TARGET_SCHEMA, raw_table)
        return
    for name, type_ in columns:
        if name not in existing:
            dwh.execute(sql.SQL("alter table {} add column {} {}").format(target, sql.Identifier(name), sql.SQL(type_)))
            log.warning(
                "app_db: source column %s.%s is new, added it to %s.%s", raw_table, name, TARGET_SCHEMA, raw_table
            )


def load_table(src: psycopg.Connection, dwh: psycopg.Connection, table: str, run_id: int) -> dict:
    primary_key = TABLES[table]
    raw_table = f"app_{table}"
    columns = source_columns(src, table)
    names = [name for name, _ in columns]
    col_list = sql.SQL(", ").join(map(sql.Identifier, names))
    target = sql.Identifier(TARGET_SCHEMA, raw_table)

    with dwh.transaction():
        ensure_raw_table(dwh, raw_table, columns, primary_key)
        watermark = meta.get_watermark(dwh, SOURCE, table)
        since = watermark - LOOKBACK if watermark else None

        # 1. Extract changed rows into a temporary table (it vanishes at commit).
        dwh.execute(
            sql.SQL("create temp table _incoming on commit drop as select {} from {} limit 0").format(col_list, target)
        )
        query = sql.SQL("select {} from {} {} order by updated_at").format(
            col_list,
            sql.Identifier(SOURCE_SCHEMA, table),
            sql.SQL("where updated_at >= %s") if since else sql.SQL(""),
        )
        extracted = 0
        with src.transaction(), src.cursor(name=f"extract_{table}") as cur:
            cur.itersize = BATCH_SIZE
            cur.execute(query, (since,) if since else None)
            with dwh.cursor().copy(sql.SQL("copy _incoming ({}) from stdin").format(col_list)) as copy:
                for row in cur:
                    copy.write_row(row)
                    extracted += 1

        # 2. Upsert into raw. Skip rows we already have at this version.
        updates = sql.SQL(", ").join(
            sql.SQL("{0} = excluded.{0}").format(sql.Identifier(n)) for n in names if n not in primary_key
        )
        results = dwh.execute(
            sql.SQL(
                """
            insert into {target} ({cols}, _loaded_at, _run_id)
            select {cols}, now(), %s from _incoming
            on conflict ({pk}) do update set {updates}, _loaded_at = excluded._loaded_at, _run_id = excluded._run_id
            where {target}.updated_at < excluded.updated_at
            returning (xmax = 0) as inserted
            """
            ).format(
                target=target, cols=col_list, pk=sql.SQL(", ").join(map(sql.Identifier, primary_key)), updates=updates
            ),
            (run_id,),
        ).fetchall()
        inserted = sum(1 for (is_new,) in results if is_new)
        updated = len(results) - inserted

        # 3. Move the watermark, in the same transaction as the data.
        new_watermark = dwh.execute("select max(updated_at) from _incoming").fetchone()[0]
        if new_watermark and (watermark is None or new_watermark > watermark):
            meta.set_watermark(dwh, SOURCE, table, new_watermark)

    log.info(
        "app_db: %-12s extracted %6d, inserted %6d, updated %6d, watermark %s",
        table,
        extracted,
        inserted,
        updated,
        new_watermark or watermark,
    )
    return {"extracted": extracted, "inserted": inserted, "updated": updated}


def load(src: psycopg.Connection, dwh: psycopg.Connection, run: meta.Run) -> None:
    for table in TABLES:
        result = load_table(src, dwh, table, run.run_id)
        run.details[table] = result
        run.rows_loaded += result["inserted"] + result["updated"]
