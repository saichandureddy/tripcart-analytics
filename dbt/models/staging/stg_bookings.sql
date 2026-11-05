with source as (
    select * from {{source('tripcart_raw','app_bookings')}}
),

renamed as (
    select booking_id,
    booking_ref,
    customer_id,
    booking_type,
    origin_airport,
    destination_airport,
    hotel_name,
    travel_start_date,
    travel_end_date,
    travellers,
    channel,
    payment_method as booking_payment_method,
    currency as currency_code,
    total_amount as total_booking_amount,
    status as booking_status,
    booked_at,
    cancelled_at as booking_cancelled_at,
    created_at,
    updated_at,
    _loaded_at,
    _run_id

    from source
)

select * from renamed