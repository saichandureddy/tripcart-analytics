with source as (
    select * from {{source('tripcart_raw','app_order_items')}}
),

renamed as (
    select order_id,
    line_no,
    product_id,
    quantity,
    unit_price,
    discount_amount,
    created_at,
    updated_at,
    _loaded_at,
    _run_id
    from source
)

select * from renamed