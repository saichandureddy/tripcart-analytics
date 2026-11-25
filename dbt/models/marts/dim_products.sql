with products as (
    select * from {{ref('stg_products')}}
),
final as (
    select products.product_id,
    products.sku,
    products.product_name,
    products.category,
    products.brand,
    products.list_price_eur as catalog_price_eur,
    products.is_active,
    products.created_at,
    products.updated_at
    from products
)
select * from final