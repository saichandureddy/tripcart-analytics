with days as (
    select generate_series('2026-01-01'::date, '2027-12-31'::date, interval '1 day')::date as date_day
),
final as (
    select date_day, 
    extract(year from date_day)::int as year,
    extract(month from date_day)::int as month_number,
    extract(day from date_day)::int as day_of_month,
    extract(quarter from date_day)::int as quarter,
    TO_CHAR(date_day, 'FMMonth') AS month_name,
    EXTRACT(DOW FROM date_day)::int as day_of_week_number,
    TO_CHAR(date_day, 'FMDay') as day_name,
    EXTRACT(DOW FROM date_day)::int IN (0, 6) AS is_weekend
    from days
) 
select * from final