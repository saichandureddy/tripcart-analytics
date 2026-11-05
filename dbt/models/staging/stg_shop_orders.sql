with source as (
    select * from {{source('tripcart_raw','app_shop_orders')}}
),

renamed as (
    select order_id,
    customer_id,
    channel,
    currency as currency_code,
    shipping_fee,
    shipping_country,
    status as shop_order_status,
    ordered_at,
    created_at,
    updated_at,
    _loaded_at,
    _run_id
    from source
)

select * from renamed