with events as (
    select * from {{ref('stg_clickstream_events')}}
),
customers as (
    select * from {{ref('int_customers_deduplicated')}}
),
event_sessions as (
    select session_id,
    max(anonymous_id) as anonymous_id,
    max(device) as device,
    max(utm_source) as utm_source,
    max(customer_id) as customer_id,
    min(event_at) as session_started_at,
    max(event_at) as session_ended_at,
    count(event_id) as number_of_events,
    count(*) filter (where event_type= 'product_view') as product_views,
    count(*) filter (where event_type= 'add_to_cart') as add_to_carts,
    count(*) filter (where event_type= 'begin_checkout') as checkouts,
    bool_or(event_type = 'purchase') as has_purchase 
    from events
    group by session_id
    
),
final as (
    select es.*,
    c.master_customer_id
    from event_sessions es
    left join customers c
    on c.customer_id=es.customer_id 
)
select * from final