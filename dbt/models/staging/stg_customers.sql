with source as (
    select * from {{source('tripcart_raw','app_customers')}}
),

renamed as (
    select customer_id,
    lower(trim(email)) as email,
    first_name,
    last_name,
    nullif(phone,'') as phone,
    country_code,
    home_airport,
    loyalty_tier,
    marketing_opt_in,
    created_at,
    updated_at,
    _loaded_at,
    _run_id
    from source
)

select * from renamed