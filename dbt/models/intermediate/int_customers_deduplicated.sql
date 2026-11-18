with customers as (
    select * from {{ ref('stg_customers') }}
),

matched as (
    select
        customer_id,
        email,
        first_value(customer_id) over (
            partition by email
            order by created_at,customer_id
        ) as master_customer_id
    from customers
)

select
    *,
    customer_id <> master_customer_id as is_duplicate_account
from matched