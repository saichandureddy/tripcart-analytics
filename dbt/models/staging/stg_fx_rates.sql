with source as (
    select * from {{source('tripcart_raw','fx_rates')}}
),

renamed as (
    select rate_date as conversion_rate_date,
    base_currency,
    quote_currency,
    rate as base_to_quote_conversion_rate,
    _loaded_at,
    _run_id
    from source
)

select * from renamed