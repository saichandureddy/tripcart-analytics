with source as (
    select * from {{source('tripcart_raw','clickstream_events')}}
),

renamed as (
    select file_key,
    line_no,
    file_modified_at,
    _loaded_at,
    _run_id,
    (event ->>'device') as device,
    (event->>'event_id')::uuid as event_id,
    (event->>'event_ts')::TIMESTAMPTZ as event_at,
    (event->>'page_url') as page_url,
    (event->>'event_type') as event_type,
    (event->'properties'->>'sku') as sku,
    (event->'properties'->>'price'):: numeric(12,2) as price,
    coalesce((event->'properties'->>'currency_code'),(event->'properties'->>'currency')) as currency_code,
    (event->>'session_id')::uuid as session_id,
    (event->>'utm_source') as utm_source,
    (event->>'app_version') as app_version,
    (event->>'customer_id'):: bigint as customer_id,
    (event->>'anonymous_id') as anonymous_id
    from source
),

deduplicated as (
    select *,
        row_number() over (partition by event_id order by file_modified_at, file_key, line_no) as row_num
    from renamed
)

select * from deduplicated where row_num = 1