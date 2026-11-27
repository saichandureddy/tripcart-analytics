with orders as (
    select * from {{ref('int_shop_orders_enriched')}}
),
fx as (
    select * from {{ref('int_fx_rates_to_usd')}}
),
final as (
    select orders.order_id as order_id,
    orders.master_customer_id as customer_id,
    orders.ordered_at::date as order_date,
    orders.channel as channel,
    orders.shipping_country as shipping_country,
    orders.shop_order_status as shop_order_status,
    orders.total_line_items as total_line_items,
    orders.currency_code as currency_code,
    orders.order_total as order_total,
    orders.items_subtotal * fx.usd_per_unit as items_subtotal_usd,
    orders.total_discount * fx.usd_per_unit as total_discount_usd,
    orders.shipping_fee   * fx.usd_per_unit as shipping_fee_usd,
    orders.order_total    * fx.usd_per_unit as order_total_usd
    from orders
    left join fx
    on fx.rate_date=orders.ordered_at::date
    AND fx.currency_code=orders.currency_code
)
select * from final