# TripCart Analytics

An end-to-end analytics pipeline for **TripCart**, a fictional online travel
company that sells trips (hotels and flights) and travel gear from one website.
Data is extracted from an operational database, website clickstream files and
a currency API, loaded into a Postgres warehouse, modelled with dbt into a star
schema, orchestrated with Airflow, and served to Power BI.

> **Status:** milestone 2 of 7. The platform runs, the simulator generates
> TripCart's data, and three loaders land it in the warehouse's `raw` schema.
> dbt models arrive in milestone 3 (see [Roadmap](#roadmap)).

## Architecture

```
 SOURCES                      ORCHESTRATION                  WAREHOUSE (Postgres)                  SERVING
 ┌────────────────────┐                                     ┌──────────────────────────────┐
 │ App DB (Postgres)  │──┐                                  │ raw → staging → intermediate │
 │ bookings, orders   │  │     ┌───────────────────┐        │        → marts (star schema) │     ┌──────────┐
 ├────────────────────┤  ├────▶│ Airflow           │───────▶│ snapshots (SCD Type 2)       │────▶│ Power BI │
 │ Clickstream JSON   │  │     │ Python loaders,   │        └──────────────────────────────┘     └──────────┘
 │ (MinIO, S3 API)    │──┤     │ then dbt (Cosmos) │                     ▲
 ├────────────────────┤  │     └───────────────────┘                     │ dbt builds and tests
 │ FX rates REST API  │──┘                                               │ the models in place
 └────────────────────┘
```

## What runs locally

`docker compose` starts nine containers. Two of them are one-off setup jobs that exit
once they are done.

| Service | What it is | Open it at |
|---|---|---|
| `postgres-app` | TripCart's operational database, the source system | `localhost:5433` |
| `postgres-dwh` | The analytics warehouse, with one schema per layer | `localhost:5434` |
| `minio` | S3-compatible file storage for clickstream files | API `localhost:9000`, console http://localhost:9001 |
| `airflow-apiserver` | Airflow's web UI and REST API | http://localhost:8080 |
| `airflow-scheduler` | Decides what runs when, and runs the tasks | |
| `airflow-dag-processor` | Reads the DAG files in `airflow/dags` | |
| `airflow-db` | Airflow's own metadata database | |
| `minio-init` | One-off: creates the clickstream bucket | |
| `airflow-init` | One-off: sets up Airflow's tables and admin login | |

## Getting started on Windows

The project runs inside **WSL 2** (a real Linux system inside Windows). Docker,
`make` and the shell scripts all behave the same there as on a Linux server or
in CI, which is the point.

### 1. One-time setup

1. **Install Ubuntu for WSL** if you don't have it yet. Docker Desktop adds
   its own `docker-desktop` distribution, but that one is only for Docker's
   internal use, not for working in. In PowerShell, run:
   ```powershell
   wsl --list --verbose          # is "Ubuntu" listed?
   wsl --install -d Ubuntu       # if not, install it
   wsl --set-default Ubuntu      # make it the default instead of docker-desktop
   ```
   The first time Ubuntu opens, it asks you to pick a Linux username and
   password. The password is for `sudo`, so remember it.
2. **Open Docker Desktop** and wait until it says *Engine running*.
3. In Docker Desktop, go to **Settings → Resources → WSL integration**, turn on
   your Ubuntu distribution, and click *Apply & restart*. This lets the
   `docker` command work inside WSL.
4. Still in Settings, check **Resources → Advanced** (or your `.wslconfig`)
   gives WSL at least **4 GB of memory**. The whole stack uses about 1.2 GB
   when idle, and Airflow needs headroom when it runs tasks.
5. Open the **Ubuntu** app from the Start menu (this is your WSL terminal) and
   install the two small tools the project uses:
   ```bash
   sudo apt update && sudo apt install -y make git
   docker --version   # should print a version, not "command not found"
   ```

### 2. Get the code

Clone the repo **inside the Linux file system** (your home folder, `~`), not
under `/mnt/c/...`. Files on the Windows drive are many times slower for
Docker and can break file permissions.

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/<your-username>/tripcart-analytics.git
cd tripcart-analytics
```

To edit the files in VS Code, run `code .` from this folder. It opens VS Code
on Windows, connected to WSL.

### 3. Create your `.env`

All passwords and ports live in a `.env` file that never goes into Git.
`.env.example` is the template that does.

```bash
make env
```

This copies `.env.example` to `.env` and generates Airflow's secret keys.
Open `.env` and replace every `change_me_...` password with your own (letters,
digits and underscores only).

### 4. Start the platform

```bash
make up
```

The first run downloads the images and builds the Airflow image, which takes
5 to 10 minutes. Later starts take about a minute. The command returns once
every service reports healthy.

### 5. Check it works

```bash
make check
```

You should see nine `OK` lines. Then:

1. Open http://localhost:8080 and log in with `AIRFLOW_ADMIN_USER` and
   `AIRFLOW_ADMIN_PASSWORD` from your `.env`.
2. Find the **platform_smoke_test** DAG, switch it on with the toggle, and
   press the ▶ *Trigger* button.
3. All four tasks should turn green. Each one connects to one part of the
   platform (app DB, warehouse, MinIO, dbt), so a red task tells you exactly
   which part is broken. Click a task, then *Logs*, to see what it printed.
4. Open the MinIO console at http://localhost:9001 (log in with
   `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`) and you will see the empty
   `tripcart-clickstream` bucket.

To connect a SQL client (DBeaver, pgAdmin, or later Power BI) to the
warehouse, use host `localhost`, port `5434`, and the `DWH_DB_*` values from
`.env`.

## Run the pipeline

With the platform up (`make up`), generate data and load it:

```bash
make simulate   # TripCart's website "runs": first time, 90 days of history (about a minute)
make load       # copy app tables, click files and FX rates into the warehouse's raw schema
make status     # row counts, watermarks and the last loader runs
```

Run `make load` a second time and `make status` again: the row counts stay
the same, because every loader only loads what is new. Run `make simulate`
later in the day and `make load` picks up just the new activity.

### The sources

| Source | What the simulator writes | Loader | Lands in |
|---|---|---|---|
| App database (`postgres-app`) | customers, products, bookings, shop orders, order items, payments | `ingestion/app_db_incremental.py`: rows changed since the `updated_at` watermark, upserted on the primary key | `raw.app_*` |
| Clickstream (MinIO) | JSON Lines files of page views, searches, carts and purchases, one folder per arrival hour | `ingestion/clickstream_files.py`: files not seen before, each loaded in one transaction | `raw.clickstream_events` |
| FX rates ([Frankfurter](https://frankfurter.dev) API) | nothing, these are real | `ingestion/fx_rates_api.py`: daily EUR rates for USD, GBP and INR, retried with backoff | `raw.fx_rates` |

The loaders' own bookkeeping (runs, watermarks, loaded files, rejected lines)
lives in the `meta` schema, so `raw` holds only source data.

### Deliberate mess

`raw` is kept exactly as it arrived. The simulator builds in problems for the
dbt layer to clean up, all tunable in `simulator/config.toml`:

- about 1% of click events are sent twice (same `event_id`)
- about 3% of click events arrive 1 to 48 hours late, in a later hour's file
- halfway through the history, click events rename `currency` to `currency_code` (schema drift)
- emails with stray capitals and spaces, phone numbers that are NULL or empty strings
- a few customers who signed up twice with differently cased emails
- order statuses that move from `placed` to `shipped` to `delivered`, sometimes `refunded`, over days
- bookings that are cancelled and refunded, failed card payments that are retried

### Tests

```bash
make test
```

The tests check the guarantees that matter: running a loader twice adds no
rows, a changed source row is updated rather than duplicated, a new source
column appears in raw, a failed load leaves nothing half-written, and the FX
loader survives timeouts. They run against throwaway databases, so your data
is never touched.

## Everyday commands

Run `make` on its own to list these.

| Command | What it does | The plain Docker command behind it |
|---|---|---|
| `make up` | Start everything and wait until healthy | `docker compose up -d --build --wait` |
| `make down` | Stop everything, keep the data | `docker compose down` |
| `make ps` | Show each container and its health | `docker compose ps -a` |
| `make logs` | Follow all logs (Ctrl+C to stop) | `docker compose logs -f` |
| `make check` | Test that every service is reachable | `./scripts/check_stack.sh` |
| `make psql-dwh` | SQL prompt on the warehouse (`\dn` lists schemas, `\q` quits) | `docker compose exec postgres-dwh psql ...` |
| `make psql-app` | SQL prompt on the app database | `docker compose exec postgres-app psql ...` |
| `make simulate` | Generate TripCart activity since the last run | `docker compose exec airflow-scheduler python -m simulator` |
| `make load` | Run the three loaders into `raw` | `docker compose exec airflow-scheduler python -m ingestion all` |
| `make status` | Row counts, watermarks and recent loader runs | `... python -m ingestion status` |
| `make test` | Run the tests | `... python -m pytest` |
| `make clean` | Stop and **delete all data** for a fresh start | `docker compose down -v` |

## Troubleshooting

- **`docker: command not found` in WSL:** Docker Desktop isn't running, or WSL
  integration is off for Ubuntu (step 1.3), or the
  terminal you opened is not Ubuntu (step 1.1).
- **`port is already allocated`:** something else on your laptop uses that
  port, often a locally installed Postgres. Change the port in `.env` (for
  example `DWH_DB_PORT=5435`) and run `make up` again.
- **A container keeps restarting:** run `docker compose logs <service>`, for
  example `docker compose logs airflow-scheduler`, and read the last lines.
- **You changed a password in `.env` after the first start:** Postgres only
  reads its password when the volume is first created. Run `make clean`, then
  `make up`. This deletes the data, which is fine at this stage.
- **Airflow is slow or containers get killed:** give WSL more memory (step 1.4).
- **`make simulate` says `service "airflow-scheduler" is not running`:** start
  the platform first with `make up`.
- **The `fx` loader fails:** it needs internet access to reach
  api.frankfurter.dev. The other two loaders still run. `make status` and the
  `error` column of `meta.load_runs` show what went wrong; just run `make load`
  again later.

## Repo layout

```
tripcart-analytics/
├── docker-compose.yml        # every service, its settings and health checks
├── Makefile                  # short commands: make up, make check, ...
├── .env.example              # template for .env (the real .env is never committed)
├── requirements.txt          # Python packages, shared by your laptop and Airflow
├── simulator/                # generates TripCart's app data and clickstream files
│   ├── config.toml           # volumes, behaviour and mess rates
│   └── reference/            # product catalog
├── ingestion/                # one loader per source, writing to the raw schema
├── tests/                    # pytest tests for the loaders and simulator
├── airflow/
│   ├── Dockerfile            # official Airflow image + dbt in its own virtualenv
│   └── dags/                 # pipeline definitions (DAGs)
├── warehouse/init/           # SQL that creates the warehouse schemas on first start
├── scripts/                  # make env and make check
└── docs/decisions.md         # why each tool and setting was chosen
```

## Roadmap

1. **Local platform** (done): Docker Compose with app Postgres,
   warehouse Postgres, MinIO and Airflow
2. **Data simulator and Python loaders** into the `raw` schema (this milestone)
3. dbt staging, intermediate and marts models
4. Data quality tests, snapshots and incremental models
5. Airflow DAG running ingestion and dbt through Astronomer Cosmos
6. CI/CD with GitHub Actions and dbt docs on GitHub Pages
7. Power BI dashboard and Databricks port
