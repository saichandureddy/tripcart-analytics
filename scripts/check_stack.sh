#!/usr/bin/env bash
# Checks each service of the running stack and prints OK or FAIL per line.
# Exits non-zero if anything failed, so CI can use it later.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

failed=0
check() {
  local name=$1; shift
  if "$@" >/dev/null 2>&1; then
    printf "  OK    %s\n" "$name"
  else
    printf "  FAIL  %s\n" "$name"; failed=1
  fi
}

echo "Checking the TripCart stack..."
check "App Postgres accepts connections" \
  docker compose exec -T postgres-app pg_isready -U "$APP_DB_USER" -d "$APP_DB_NAME"
check "Warehouse Postgres accepts connections" \
  docker compose exec -T postgres-dwh pg_isready -U "$DWH_DB_USER" -d "$DWH_DB_NAME"
check "Warehouse has raw/staging/intermediate/marts/snapshots schemas" \
  bash -c "[ \"\$(docker compose exec -T postgres-dwh psql -U '$DWH_DB_USER' -d '$DWH_DB_NAME' -tAc \
    \"select count(*) from information_schema.schemata where schema_name in ('raw','staging','intermediate','marts','snapshots')\")\" = 5 ]"
check "MinIO is healthy" \
  docker compose exec -T minio curl -fs http://localhost:9000/minio/health/live
check "MinIO bucket '$MINIO_BUCKET' exists" \
  docker compose exec -T minio sh -c "mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" && mc ls local/$MINIO_BUCKET"
check "Airflow API server and UI respond" \
  curl -fs "http://localhost:${AIRFLOW_PORT:-8080}/api/v2/monitor/health"
check "Airflow scheduler is healthy" \
  docker compose exec -T airflow-scheduler curl -fs http://localhost:8974/health
check "Airflow loaded the smoke test DAG without errors" \
  bash -c "docker compose exec -T airflow-scheduler airflow dags list -o plain | grep -q platform_smoke_test"
check "dbt is installed in the Airflow image" \
  docker compose exec -T airflow-scheduler /opt/tripcart-venv/bin/dbt --version

if [[ $failed -eq 0 ]]; then
  echo "All checks passed. Airflow UI: http://localhost:${AIRFLOW_PORT:-8080}"
else
  echo "Some checks failed. 'make ps' shows container status, 'make logs' shows output."
  exit 1
fi
