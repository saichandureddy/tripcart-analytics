with orders as (
    select * from {{ref('stg_shop_orders')}}
),
items as (
    select * from {{ref('stg_order_items')}}
),
customers as (
    select * from {{ref('int_customers_deduplicated')}}
),

item_totals as (
    select order_id,
    count(line_no) as total_line_items,
    sum(quantity * unit_price) as items_subtotal,
    sum(discount_amount) as total_discount
    from items
    group by order_id
),
final as (
    select orders.*,
    customers.master_customer_id,
    coalesce(item_totals.total_line_items,0) as total_line_items,
    coalesce(item_totals.items_subtotal,0) as items_subtotal, 
    coalesce(item_totals.total_discount,0) as total_discount,
    coalesce(item_totals.items_subtotal,0) - coalesce(item_totals.total_discount,0) + coalesce(orders.shipping_fee,0) as order_total
    from orders
    left join item_totals on item_totals.order_id=orders.order_id
    left join customers on customers.customer_id=orders.customer_id
)
select * from final