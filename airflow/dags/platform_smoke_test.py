"""Checks that Airflow can reach every part of the TripCart platform.

Trigger it by hand from the Airflow UI after `make up`. All four tasks
should turn green. If one fails, its log says which service is unreachable.
"""

import os
import subprocess

import pendulum
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task

WAREHOUSE_SCHEMAS = {"raw", "staging", "intermediate", "marts", "snapshots"}


@dag(
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["tripcart", "platform"],
)
def platform_smoke_test():
    @task
    def check_app_db():
        version = PostgresHook(postgres_conn_id="tripcart_app").get_first("select version()")[0]
        print(f"App database is up: {version}")

    @task
    def check_warehouse():
        hook = PostgresHook(postgres_conn_id="tripcart_dwh")
        rows = hook.get_records("select schema_name from information_schema.schemata")
        missing = WAREHOUSE_SCHEMAS - {row[0] for row in rows}
        if missing:
            raise ValueError(f"Warehouse is missing schemas: {sorted(missing)}")
        print(f"Warehouse has all layer schemas: {sorted(WAREHOUSE_SCHEMAS)}")

    @task
    def check_minio():
        bucket = os.environ["TRIPCART_MINIO_BUCKET"]
        if not S3Hook(aws_conn_id="tripcart_minio").check_for_bucket(bucket):
            raise ValueError(f"MinIO bucket {bucket!r} does not exist")
        print(f"MinIO bucket {bucket!r} exists")

    @task
    def check_dbt():
        result = subprocess.run(
            [os.environ["DBT_EXECUTABLE"], "--version"], capture_output=True, text=True, check=True
        )
        print(result.stdout)

    [check_app_db(), check_warehouse(), check_minio(), check_dbt()]


platform_smoke_test()
