#!/usr/bin/env bash
# Creates .env from .env.example and fills in the values that should be
# generated rather than typed: your user id and Airflow's two secrets.
# Refuses to overwrite an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists, leaving it alone. Delete it first to regenerate."
  exit 0
fi

fernet_key=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
jwt_secret=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")

sed -e "s|^AIRFLOW_UID=.*|AIRFLOW_UID=$(id -u)|" \
    -e "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${fernet_key}|" \
    -e "s|^AIRFLOW_JWT_SECRET=.*|AIRFLOW_JWT_SECRET=${jwt_secret}|" \
    .env.example > .env

echo "Created .env. Open it and change the change_me_ passwords before 'make up'."
