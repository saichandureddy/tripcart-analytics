with source as (
    select * from {{source('tripcart_raw','app_payments')}}
),

renamed as (
    select payment_id,
    booking_id,
    order_id,
    payment_type,
    method as payment_method,
    status as payment_status,
    currency as currency_code,
    amount as payment_amount,
    paid_at,
    created_at,
    updated_at,
    _loaded_at,
    _run_id
    from source
)

select * from renamed