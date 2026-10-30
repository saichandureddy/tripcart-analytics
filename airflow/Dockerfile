# TripCart's Airflow image: the official image plus our pipeline packages.
FROM apache/airflow:3.3.2-python3.12

# dbt and the loaders go in their own virtualenv, not into Airflow's Python.
# That keeps their dependencies from clashing with Airflow's, and it is the
# setup Astronomer Cosmos expects when it runs dbt (milestone 5).
USER root
RUN mkdir -p /opt/tripcart-venv && chown airflow:0 /opt/tripcart-venv
USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/tripcart-venv \
    && /opt/tripcart-venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

ENV DBT_EXECUTABLE=/opt/tripcart-venv/bin/dbt
