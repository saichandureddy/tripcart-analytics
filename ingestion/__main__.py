"""Run the loaders: python -m ingestion <app|clickstream|fx|all|status>  (or `make load`).

app          app database tables -> raw.app_*        (incremental, by updated_at)
clickstream  MinIO JSON Lines files -> raw.clickstream_events (new files only)
fx           Frankfurter API -> raw.fx_rates         (with retries)
all          all three, in that order
status       row counts, watermarks and the last runs
"""

import argparse
import logging
import sys

import psycopg

from ingestion import app_db_incremental, clickstream_files, fx_rates_api, meta, settings

log = logging.getLogger("ingestion")


def run_app(dwh):
    with meta.track_run(dwh, "app_db") as run, psycopg.connect(settings.app_db_url(), autocommit=True) as src:
        app_db_incremental.load(src, dwh, run)


def run_clickstream(dwh):
    with meta.track_run(dwh, "clickstream") as run:
        clickstream_files.load(dwh, settings.s3_client(), settings.s3_bucket(), run)


def run_fx(dwh):
    with meta.track_run(dwh, "fx_rates") as run:
        fx_rates_api.load(dwh, run)


LOADERS = {"app": run_app, "clickstream": run_clickstream, "fx": run_fx}


def status(dwh) -> None:
    meta.ensure_meta(dwh)
    print("Rows in the raw schema:")
    tables = [
        r[0]
        for r in dwh.execute("select table_name from information_schema.tables where table_schema = 'raw' order by 1")
    ]
    for table in tables:
        count = dwh.execute(f'select count(*) from raw."{table}"').fetchone()[0]
        print(f"  raw.{table:<22} {count:>9,}")
    if not tables:
        print("  (none yet: run `make load`)")
    print("\nWatermarks (app tables loaded up to):")
    for source, obj, wm in dwh.execute("select source, object, watermark from meta.watermarks order by 1, 2"):
        print(f"  {source}.{obj:<15} {wm:%Y-%m-%d %H:%M:%S %Z}")
    files = dwh.execute("select count(*) from meta.loaded_files").fetchone()[0]
    print(f"\nClickstream files loaded: {files:,}")
    print("\nLast 6 loader runs:")
    for row in dwh.execute(
        "select run_id, source, status, rows_loaded, started_at, finished_at - started_at "
        "from meta.load_runs order by run_id desc limit 6"
    ):
        run_id, source, state, rows, started, took = row
        took = f"{took.total_seconds():.1f}s" if took else "-"
        print(
            f"  #{run_id:<4} {source:<12} {state:<10} {rows if rows is not None else '-':>8} rows  "
            f"{started:%Y-%m-%d %H:%M}  {took}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=[*LOADERS, "all", "status"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    with psycopg.connect(settings.dwh_db_url(), autocommit=True) as dwh:
        if args.what == "status":
            status(dwh)
            return 0
        failed = []
        for name in LOADERS if args.what == "all" else [args.what]:
            try:
                LOADERS[name](dwh)
            except Exception as exc:  # noqa: BLE001 - keep going, so one broken source doesn't block the others
                log.error("%s loader failed: %s", name, exc)
                failed.append(name)
        if failed:
            log.error("Failed: %s. Details are in meta.load_runs.", ", ".join(failed))
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
