-- TripCart's operational ("app") database: the tables the website writes to.
-- The simulator creates them on its first run. Every table has an
-- updated_at column that changes whenever a row changes, which is what the
-- incremental loader uses as its watermark.

create table if not exists customers (
    customer_id      bigint primary key,
    email            text not null,
    first_name       text,
    last_name        text,
    phone            text,
    country_code     char(2) not null,
    home_airport     char(3),
    loyalty_tier     text not null,          -- basic, silver, gold
    marketing_opt_in boolean not null,
    created_at       timestamptz not null,
    updated_at       timestamptz not null
);

create table if not exists products (
    product_id     bigint primary key,
    sku            text not null unique,
    product_name   text not null,
    category       text not null,
    brand          text not null,
    list_price_eur numeric(10, 2) not null,
    is_active      boolean not null,
    created_at     timestamptz not null,
    updated_at     timestamptz not null
);

-- Trip bookings: one row per hotel stay or return flight.
create table if not exists bookings (
    booking_id        bigint primary key,
    booking_ref       text not null unique,
    customer_id       bigint not null references customers,
    booking_type      text not null,         -- hotel, flight
    origin_airport    char(3),               -- flights only
    destination_airport char(3) not null,
    hotel_name        text,                  -- hotels only
    travel_start_date date not null,
    travel_end_date   date not null,
    travellers        int not null,
    channel           text not null,         -- web, mobile_app, partner
    payment_method    text not null,         -- card, paypal, pay_later
    currency          char(3) not null,
    total_amount      numeric(12, 2) not null,
    status            text not null,         -- confirmed, cancelled, completed
    booked_at         timestamptz not null,
    cancelled_at      timestamptz,
    created_at        timestamptz not null,
    updated_at        timestamptz not null
);

-- Travel shop orders and their lines.
create table if not exists shop_orders (
    order_id         bigint primary key,
    customer_id      bigint not null references customers,
    channel          text not null,
    currency         char(3) not null,
    shipping_fee     numeric(10, 2) not null,
    shipping_country char(2) not null,
    status           text not null,          -- placed, shipped, delivered, cancelled, refunded
    ordered_at       timestamptz not null,
    created_at       timestamptz not null,
    updated_at       timestamptz not null
);

create table if not exists order_items (
    order_id        bigint not null references shop_orders,
    line_no         int not null,
    product_id      bigint not null references products,
    quantity        int not null,
    unit_price      numeric(10, 2) not null,  -- in the order's currency
    discount_amount numeric(10, 2) not null,
    created_at      timestamptz not null,
    updated_at      timestamptz not null,
    primary key (order_id, line_no)
);

-- Money movements: charges and refunds, for bookings and for shop orders.
create table if not exists payments (
    payment_id   bigint primary key,
    booking_id   bigint references bookings,
    order_id     bigint references shop_orders,
    payment_type text not null,              -- charge, refund
    method       text not null,
    status       text not null,              -- succeeded, failed
    currency     char(3) not null,
    amount       numeric(12, 2) not null,    -- always positive; refunds are money out
    paid_at      timestamptz not null,
    created_at   timestamptz not null,
    updated_at   timestamptz not null
);

create index if not exists customers_updated_at_idx on customers (updated_at);
create index if not exists products_updated_at_idx on products (updated_at);
create index if not exists bookings_updated_at_idx on bookings (updated_at);
create index if not exists shop_orders_updated_at_idx on shop_orders (updated_at);
create index if not exists order_items_updated_at_idx on order_items (updated_at);
create index if not exists payments_updated_at_idx on payments (updated_at);

-- The simulator's own bookkeeping. Tables starting with _sim are not part of
-- the "app" and the loaders ignore them.
create table if not exists _sim_state (
    key   text primary key,
    value text not null
);

-- Things scheduled to happen later: an order ships, a booking is cancelled.
create table if not exists _sim_plan (
    plan_id   bigserial primary key,
    due_at    timestamptz not null,
    action    text not null,
    entity_id bigint not null
);
create index if not exists _sim_plan_due_at_idx on _sim_plan (due_at);

-- Click events that "arrive late": generated already, but not uploaded until
-- their arrival time has passed.
create table if not exists _sim_pending_events (
    arrival_at timestamptz not null,
    event      jsonb not null
);
