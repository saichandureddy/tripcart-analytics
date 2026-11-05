with source as (
    select * from {{source('tripcart_raw','app_products')}}
),

renamed as (
    select product_id,
    sku,
    product_name,
    category,
    brand,
    list_price_eur,
    is_active,
    created_at,
    updated_at,
    _loaded_at,
    _run_id
    from source
)

select * from renamed
