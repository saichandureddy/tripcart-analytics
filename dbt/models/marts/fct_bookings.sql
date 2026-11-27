with booking_details as (
    select * from {{ref('stg_bookings')}}
),
customers as (
    select * from {{ref('int_customers_deduplicated')}}
),
fx as (
    select * from {{ref('int_fx_rates_to_usd')}}
),
final as (
    select booking_details.booking_id as booking_id,
    booking_details.booking_ref as booking_ref,
    customers.master_customer_id as customer_id,
    booking_details.booking_type as booking_type,
    booking_details.origin_airport as origin_airport,
    booking_details.destination_airport as destination_airport,
    booking_details.hotel_name as hotel_name,
    booking_details.travel_start_date::date as travel_start_date,
    booking_details.travel_end_date::date as travel_end_date,
    booking_details.travellers as no_of_travellers,
    booking_details.channel as channel,
    booking_details.booking_payment_method as booking_payment_method,
    booking_details.booking_status as booking_status,
    booking_details.booked_at::date as booking_date,
    booking_details.cancelled_at::date as cancelled_date,
    booking_details.cancelled_at is not null as is_cancelled,
    booking_details.currency_code as currency_code,
    booking_details.total_booking_amount as total_booking_amount,
    booking_details.total_booking_amount * fx.usd_per_unit as total_booking_amount_usd
    from booking_details
    left join customers on
    customers.customer_id=booking_details.customer_id
    left join fx
    on fx.rate_date=booking_details.booked_at::date
    AND fx.currency_code=booking_details.currency_code
)
select * from final