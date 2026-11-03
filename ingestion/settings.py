"""Connection settings for the simulator and the loaders.

Inside Docker, docker-compose.yml sets the TRIPCART_* variables, pointing at
the container names (postgres-app, minio, ...). When you run the code on your
laptop instead, they are built from .env with localhost and the host ports.
"""

import os
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Values already set in the environment win over .env.
load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"{name} is not set. Did you run `make env`?")
    return value


def app_db_url() -> str:
    """TripCart's operational database, the source system."""
    return os.environ.get("TRIPCART_APP_DB_URL") or (
        f"postgresql://{_env('APP_DB_USER')}:{_env('APP_DB_PASSWORD')}"
        f"@localhost:{_env('APP_DB_PORT', '5433')}/{_env('APP_DB_NAME')}"
    )


def dwh_db_url() -> str:
    """The analytics warehouse, where the loaders write."""
    return os.environ.get("TRIPCART_DWH_DB_URL") or (
        f"postgresql://{_env('DWH_DB_USER')}:{_env('DWH_DB_PASSWORD')}"
        f"@localhost:{_env('DWH_DB_PORT', '5434')}/{_env('DWH_DB_NAME')}"
    )


def s3_bucket() -> str:
    return _env("MINIO_BUCKET", "tripcart-clickstream")


def s3_client():
    """A boto3 S3 client for MinIO. The same code would work against AWS S3."""
    endpoint = os.environ.get("TRIPCART_S3_ENDPOINT") or f"http://localhost:{_env('MINIO_API_PORT', '9000')}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=_env("MINIO_ROOT_USER"),
        aws_secret_access_key=_env("MINIO_ROOT_PASSWORD"),
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def fx_api_url() -> str:
    return os.environ.get("TRIPCART_FX_API_URL", "https://api.frankfurter.dev/v2")
