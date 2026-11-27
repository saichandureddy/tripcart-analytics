with order_items as (
    select * from {{ref('stg_order_items')}}
),
orders as (
    select * from {{ref('int_shop_orders_enriched')}}
),
fx as (
    select * from {{ref('int_fx_rates_to_usd')}}
),
final as (
    select order_items.order_id || '-' || order_items.line_no as order_item_id,
    order_items.order_id as order_id,
    order_items.line_no as line_no,
    order_items.product_id as product_id,
    orders.master_customer_id as customer_id,
    orders.ordered_at::date as order_date,
    order_items.quantity as quantity,
    order_items.unit_price as unit_price,
    order_items.discount_amount as discount_amount,
    order_items.quantity * order_items.unit_price - coalesce(order_items.discount_amount,0) as line_total,
    orders.currency_code as currency_code,
    order_items.unit_price * fx.usd_per_unit as unit_price_usd,
    order_items.discount_amount * fx.usd_per_unit as discount_amount_usd,
    ((order_items.quantity*order_items.unit_price) - coalesce(order_items.discount_amount,0)) * fx.usd_per_unit as line_total_usd
    from order_items
    left join orders on
    orders.order_id=order_items.order_id
    left join fx
    on fx.rate_date=orders.ordered_at::date
    AND fx.currency_code=orders.currency_code
)
select * from final
