with customers as (
    select * from {{ref('stg_customers')}}
),

accounts as (
    select * from {{ref('int_customers_deduplicated')}}
),

final as (
    select customers.customer_id,
    customers.email,
    customers.first_name,
    customers.last_name,
    customers.phone,
    customers.country_code,
    customers.home_airport,
    customers.loyalty_tier,
    customers.marketing_opt_in,
    customers.created_at as signed_up_at,
    customers.updated_at
    from customers
    join accounts on accounts.customer_id=customers.customer_id
    where not accounts.is_duplicate_account
)
select * from final