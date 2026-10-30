# Decisions

One short entry per choice: what was decided, why, and what was rejected.
Useful for reviewers, and good practice for answering "why did you..." in an
interview.

## 1. Docker Compose for the whole local platform

Every service is defined in one `docker-compose.yml`, so anyone can clone the
repo and start the same platform with `make up`. The same file will run in CI
later. Installing Postgres and Airflow directly on the laptop was rejected: it
is hard to reproduce and hard to reset.

## 2. Separate app database and warehouse

TripCart's operational database (`postgres-app`) and the analytics warehouse
(`postgres-dwh`) are two Postgres instances. In a real company these are
different systems owned by different teams, and analytics queries must never
slow down the app. Keeping them apart forces the pipeline to do a real
extract and load instead of querying the source directly.

## 3. One warehouse schema per layer

`raw`, `staging`, `intermediate`, `marts` and `snapshots` are created on the
first start. Each layer has one job (land, clean, combine, serve, keep
history), which makes it obvious where a piece of logic belongs and lets
Power BI see only `marts`.

## 4. Airflow's metadata in its own database

Airflow stores its run history in a third, small Postgres (`airflow-db`).
Mixing it into the warehouse would let a dbt mistake damage Airflow's state,
and production setups keep them apart for the same reason.

## 5. LocalExecutor, no Celery or Redis

Airflow runs tasks with the LocalExecutor, inside the scheduler container.
CeleryExecutor (the official Compose default) adds Redis and worker
containers, which cost memory on a laptop and add nothing for one machine.
Switching later is a one-line change.

## 6. dbt in its own virtualenv inside the Airflow image

dbt is installed into `/opt/tripcart-venv`, not into Airflow's Python. dbt and
Airflow pin many of the same libraries at different versions, and Astronomer
Cosmos is designed to call a dbt executable in a separate environment.

## 7. dbt Core v1, pinned below 2.0

`requirements.txt` pins `dbt-core>=1.10,<2.0` and `dbt-postgres>=1.9,<2.0`.
As of October 2026, dbt v2 has no Postgres adapter and Cosmos does not
support it yet. Re-check before milestone 7.

## 8. Community MinIO image

The official `minio/minio` images were withdrawn from Docker Hub in 2025.
The project uses `pgsty/minio`, a maintained build of the same open-source
MinIO server, pinned to a release tag. The ingestion code only talks the S3
API through `boto3`, so swapping in another S3-compatible store (SeaweedFS,
Garage, or real AWS S3) only changes the endpoint URL.

## 9. Secrets in `.env`, connections as environment variables

Passwords live in `.env`, which is in `.gitignore`; `.env.example` documents
every setting without real values. Airflow connections are defined as
`AIRFLOW_CONN_*` environment variables built from `.env`, so they are
versioned in code (minus the secrets) and never have to be clicked together
in the UI.

## 10. Images pinned to exact versions

Postgres 16, Airflow 3.3.2 and the MinIO release are pinned. `latest` tags
change under you, and "it worked last week" is a bad answer when a build
breaks.
