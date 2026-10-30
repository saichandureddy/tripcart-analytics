-- Runs once, the first time the warehouse container starts with an empty volume.
-- One schema per layer, so data flows left to right:
--   raw -> staging -> intermediate -> marts, with snapshots holding history.

create schema if not exists raw;           -- loaders write here, untouched
create schema if not exists staging;       -- dbt: cleaned and renamed, 1:1 with sources
create schema if not exists intermediate;  -- dbt: joins and business logic
create schema if not exists marts;         -- dbt: star schema and reporting tables
create schema if not exists snapshots;     -- dbt: SCD Type 2 history

comment on schema raw is 'Landing zone: data exactly as extracted from sources';
comment on schema staging is 'dbt staging models: typed, renamed, deduplicated';
comment on schema intermediate is 'dbt intermediate models: joins, currency conversion';
comment on schema marts is 'dbt marts: facts, dimensions and rpt_ tables for BI';
comment on schema snapshots is 'dbt snapshots: slowly changing history';
