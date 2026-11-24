with fx as (
    select * from {{ ref('stg_fx_rates') }}
),

usd as (
    select conversion_rate_date, base_to_quote_conversion_rate as eur_to_usd
    from fx
    where quote_currency = 'USD'
),

quote_currencies as (
    select fx.conversion_rate_date as rate_date,
           fx.quote_currency as currency_code,
           usd.eur_to_usd / fx.base_to_quote_conversion_rate as usd_per_unit
    from fx
    join usd on usd.conversion_rate_date = fx.conversion_rate_date
),

eur as (
    select conversion_rate_date as rate_date, 'EUR' as currency_code, eur_to_usd as usd_per_unit
    from usd
)

select * from quote_currencies
union all
select * from eur