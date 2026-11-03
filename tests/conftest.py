"""Shared test setup.

The tests need a real Postgres, because the loaders' guarantees (upserts,
transactions, COPY) are Postgres features and a fake would prove nothing.
Each test run creates two throwaway databases on the warehouse server, one
playing the app database and one the warehouse, and drops them at the end.
Your real data is never touched.

MinIO and the FX API are replaced by small fakes, so the tests run offline.
"""

import io
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from ingestion import meta, settings
from simulator.engine import Simulator, load_config

# A fixed "now", so simulated data does not depend on when the tests run.
UNTIL = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _db_url(name: str) -> str:
    base = psycopg.conninfo.conninfo_to_dict(settings.dwh_db_url())
    base["dbname"] = name
    return psycopg.conninfo.make_conninfo(**base)


@pytest.fixture(scope="session")
def database_urls():
    suffix = uuid.uuid4().hex[:8]
    names = {"app": f"test_app_{suffix}", "dwh": f"test_dwh_{suffix}"}
    with psycopg.connect(settings.dwh_db_url(), autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'create database "{name}"')
    yield {role: _db_url(name) for role, name in names.items()}
    with psycopg.connect(settings.dwh_db_url(), autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def src(database_urls):
    """An empty app database for one test."""
    with psycopg.connect(database_urls["app"], autocommit=True) as conn:
        conn.execute("drop schema public cascade; create schema public")
        yield conn


@pytest.fixture
def dwh(database_urls):
    """An empty warehouse for one test."""
    with psycopg.connect(database_urls["dwh"], autocommit=True) as conn:
        conn.execute("drop schema if exists raw cascade; drop schema if exists meta cascade")
        meta.ensure_meta(conn)
        yield conn


class FakeS3:
    """Just enough of boto3's S3 client for the simulator and the loader."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix=""):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        yield {"Contents": [{"Key": k, "Size": len(self.objects[k]), "ETag": f'"{hash(k)}"'} for k in keys]}


@pytest.fixture
def s3():
    return FakeS3()


@pytest.fixture
def small_config():
    """The real config, scaled down so a simulated week takes about a second."""
    config = load_config()
    config["volume"].update(
        backfill_days=7,
        initial_customers=40,
        new_customers_per_day=3,
        bookings_per_day=8,
        orders_per_day=10,
        browse_sessions_per_day=20,
    )
    config["mess"]["schema_drift_after_days"] = 3
    return config


@pytest.fixture
def simulate(src, s3, small_config):
    """Call simulate(until) to run the simulator against the test app database."""

    def run(until=UNTIL, seed=7):
        return Simulator(src, s3, "test-bucket", small_config, seed=seed).run(until)

    return run


def raw_count(dwh, table: str) -> int:
    return dwh.execute(f"select count(*) from raw.{table}").fetchone()[0]
