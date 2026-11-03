"""The simulator: pretends to be TripCart's website and writes what it does.

Each run moves a clock forward from where the last run stopped to "now" and
generates everything that would have happened in between: sign-ups, trip
bookings, shop orders, payments, website clicks, and later status changes
(an order ships, a booking is cancelled). The first run starts
`backfill_days` in the past, so you get a few months of history at once.

Every row gets the time it "happened" as created_at / updated_at, the way a
real app's database would. That is what makes the loader's updated_at
watermark work.

Work is committed one simulated day at a time, so stopping a long first run
halfway loses nothing: the next run carries on from the last finished day.
"""

import math
import random
import tomllib
import unicodedata
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import psycopg
from faker import Faker
from psycopg.types.json import Jsonb

from simulator import clickstream, reference

DAY = timedelta(days=1)
SCHEMA_SQL = Path(__file__).parent / "schema.sql"
CONFIG_TOML = Path(__file__).parent / "config.toml"


def load_config(path: Path = CONFIG_TOML) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def ascii_slug(text: str) -> str:
    """José -> jose, so names can go into email addresses."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(ch for ch in plain.lower() if ch.isalpha())


class Simulator:
    def __init__(self, conn: psycopg.Connection, s3, bucket: str, config: dict, seed: int | None = None):
        self.conn = conn
        self.s3 = s3
        self.bucket = bucket
        self.vol = config["volume"]
        self.beh = config["behaviour"]
        self.mess = config["mess"]
        self.rng = random.Random(seed)
        self.fakers = {}
        for code, country in reference.COUNTRIES.items():
            fake = Faker(country["locale"])
            fake.seed_instance(self.rng.getrandbits(32))
            self.fakers[code] = fake
        self.counts: dict[str, int] = {}
        self._events: list[tuple[datetime, dict]] = []

    # ------------------------------------------------------------------ run
    def run(self, until: datetime | None = None) -> dict:
        """Simulate everything from the last run up to `until` (default: now)."""
        until = until or utc_now()
        with self.conn.transaction():
            self.conn.execute(SCHEMA_SQL.read_text())
        state = self._read_state()

        if "last_tick" in state:
            start = datetime.fromisoformat(state["last_tick"])
            self.drift_at = datetime.fromisoformat(state["drift_at"])
        else:
            start = (until - timedelta(days=self.vol["backfill_days"])).replace(minute=0, second=0)
            self.drift_at = start + timedelta(days=self.mess["schema_drift_after_days"])
            with self.conn.transaction():
                self._write_state(drift_at=self.drift_at.isoformat(), last_tick=start.isoformat())
                self._seed_catalog_and_customers(start)

        self._load_caches()
        t = start
        while t < until:
            end = min(t + DAY, until)
            with self.conn.transaction():
                self._simulate_window(t, end)
                self._write_state(last_tick=end.isoformat())
            self._upload_arrived_events(end)
            t = end
        return {"from": start, "to": until, **self.counts}

    # -------------------------------------------------------- bookkeeping
    def _count(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n

    def _read_state(self) -> dict:
        return dict(self.conn.execute("select key, value from _sim_state").fetchall())

    def _write_state(self, **values: str) -> None:
        for key, value in values.items():
            self.conn.execute(
                "insert into _sim_state values (%s, %s) on conflict (key) do update set value = excluded.value",
                (key, value),
            )

    def _next_id(self, table: str) -> int:
        self._ids[table] += 1
        return self._ids[table]

    def _load_caches(self) -> None:
        q = self.conn.execute
        self._ids = {
            t: q(f"select coalesce(max({c}), 0) from {t}").fetchone()[0]
            for t, c in [
                ("customers", "customer_id"),
                ("bookings", "booking_id"),
                ("shop_orders", "order_id"),
                ("payments", "payment_id"),
                ("products", "product_id"),
            ]
        }
        self.customers = [
            {"id": r[0], "country": r[1], "airport": r[2], "currency": reference.COUNTRIES[r[1]]["currency"]}
            for r in q("select customer_id, country_code, home_airport from customers order by customer_id")
        ]
        self.products = [
            {"id": r[0], "sku": r[1], "category": r[2], "price_eur": float(r[3])}
            for r in q("select product_id, sku, category, list_price_eur from products where is_active")
        ]

    def _poisson(self, mean: float) -> int:
        """How many things happen in a window, when `mean` happen on average."""
        if mean > 50:
            return max(0, round(self.rng.gauss(mean, math.sqrt(mean))))
        limit, k, p = math.exp(-mean), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def _time_in(self, a: datetime, b: datetime) -> datetime:
        return a + (b - a) * self.rng.random()

    def _plan(self, due_at: datetime, action: str, entity_id: int) -> None:
        self.conn.execute(
            "insert into _sim_plan (due_at, action, entity_id) values (%s, %s, %s)", (due_at, action, entity_id)
        )

    # ------------------------------------------------------------ seeding
    def _seed_catalog_and_customers(self, start: datetime) -> None:
        launched = start - timedelta(days=400)
        self._ids = {"customers": 0}
        for i, p in enumerate(reference.load_products(), start=1):
            self.conn.execute(
                "insert into products values (%s, %s, %s, %s, %s, %s, true, %s, %s)",
                (i, p["sku"], p["product_name"], p["category"], p["brand"], p["list_price_eur"], launched, launched),
            )
        self.customers = []
        for _ in range(self.vol["initial_customers"]):
            self._create_customer(self._time_in(start - timedelta(days=730), start))

    # ------------------------------------------------------- one window
    def _simulate_window(self, a: datetime, b: datetime) -> None:
        share_of_day = (b - a) / DAY
        weekday = reference.WEEKDAY_DEMAND[a.weekday()]
        month = reference.MONTH_DEMAND[a.month]

        for _ in range(self._poisson(self.vol["new_customers_per_day"] * share_of_day)):
            self._create_customer(self._time_in(a, b))
        for _ in range(self._poisson(len(self.customers) * self.beh["customer_change_rate"] * share_of_day)):
            self._change_customer(self._time_in(a, b))
        for _ in range(self._poisson(len(self.products) * self.beh["price_change_rate"] * share_of_day)):
            self._change_price(self._time_in(a, b))
        for _ in range(self._poisson(self.vol["bookings_per_day"] * month * share_of_day)):
            self._create_booking(self._time_in(a, b))
        for _ in range(self._poisson(self.vol["orders_per_day"] * weekday * share_of_day)):
            self._create_order(self._time_in(a, b), self.rng.choice(self.customers))
        for _ in range(self._poisson(self.vol["browse_sessions_per_day"] * weekday * share_of_day)):
            self._browse_session(self._time_in(a, b))

        self._apply_due_plans(b)
        self.conn.cursor().executemany(
            "insert into _sim_pending_events (arrival_at, event) values (%s, %s)",
            [(arrival, Jsonb(event)) for arrival, event in self._events],
        )
        self._events = []

    def _apply_due_plans(self, until: datetime) -> None:
        """Run every planned change that is due. A change can plan the next one."""
        while True:
            due = self.conn.execute(
                "delete from _sim_plan where plan_id in "
                "(select plan_id from _sim_plan where due_at < %s order by due_at limit 500) "
                "returning due_at, action, entity_id",
                (until,),
            ).fetchall()
            if not due:
                return
            for due_at, action, entity_id in sorted(due):
                getattr(self, f"_on_{action}")(due_at, entity_id)

    # ---------------------------------------------------------- customers
    def _create_customer(self, ts: datetime) -> None:
        mess = self.mess
        cid = self._next_id("customers")
        if self.customers and self.rng.random() < mess["duplicate_customer_rate"]:
            # Someone signs up a second time with the same email, typed differently.
            first, last, email, phone, country, airport = self.conn.execute(
                "select first_name, last_name, email, phone, country_code, home_airport "
                "from customers where customer_id = %s",
                (self.rng.choice(self.customers)["id"],),
            ).fetchone()
            email = email.strip().upper() if email.islower() else email.strip().lower()
            self._count("duplicate_customers")
        else:
            country = self.rng.choices(list(reference.COUNTRIES), [c["weight"] for c in reference.COUNTRIES.values()])[
                0
            ]
            fake = self.fakers[country]
            first, last = fake.first_name(), fake.last_name()
            email = f"{ascii_slug(first)}.{ascii_slug(last)}{self.rng.randint(1, 999)}@{fake.free_email_domain()}"
            email = self._maybe_messy_email(email)
            phone = self._maybe_phone(country)
            airport = self.rng.choice(reference.COUNTRIES[country]["airports"])
        tier = self.rng.choices(["basic", "silver", "gold"], [80, 15, 5])[0]
        self.conn.execute(
            "insert into customers values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cid, email, first, last, phone, country, airport, tier, self.rng.random() < 0.4, ts, ts),
        )
        self.customers.append(
            {"id": cid, "country": country, "airport": airport, "currency": reference.COUNTRIES[country]["currency"]}
        )
        self._count("customers_created")

    def _maybe_messy_email(self, email: str) -> str:
        if self.rng.random() >= self.mess["messy_email_rate"]:
            return email
        user, domain = email.split("@")
        return self.rng.choice(
            [f"{user.capitalize()}@{domain}", f"{user}@{domain.upper()}", f" {email}", f"{email} ", email.upper()]
        )

    def _maybe_phone(self, country: str) -> str | None:
        roll = self.rng.random()
        rate = self.mess["null_phone_rate"]
        if roll < rate * 2 / 3:
            return None
        if roll < rate:
            return ""  # the form saved an empty string instead of NULL
        return self.fakers[country].phone_number()

    def _change_customer(self, ts: datetime) -> None:
        cust = self.rng.choice(self.customers)
        change = self.rng.choices(["tier", "phone", "email", "opt_in"], [50, 25, 15, 10])[0]
        if change == "tier":
            sql = (
                "update customers set loyalty_tier = case loyalty_tier when 'basic' then 'silver' "
                "else 'gold' end, updated_at = %s where customer_id = %s"
            )
        elif change == "phone":
            sql = "update customers set phone = %s, updated_at = %s where customer_id = %s"
            self.conn.execute(sql, (self._maybe_phone(cust["country"]), ts, cust["id"]))
            self._count("customers_changed")
            return
        elif change == "email":
            sql = "update customers set email = lower(trim(email)), updated_at = %s where customer_id = %s"
        else:
            sql = "update customers set marketing_opt_in = not marketing_opt_in, updated_at = %s where customer_id = %s"
        self.conn.execute(sql, (ts, cust["id"]))
        self._count("customers_changed")

    def _change_price(self, ts: datetime) -> None:
        product = self.rng.choice(self.products)
        new_price = round(product["price_eur"] * self.rng.uniform(0.9, 1.12), 2)
        product["price_eur"] = new_price
        self.conn.execute(
            "update products set list_price_eur = %s, updated_at = %s where product_id = %s",
            (new_price, ts, product["id"]),
        )
        self._count("price_changes")

    # ----------------------------------------------------------- payments
    def _charge(
        self,
        ts: datetime,
        amount: float,
        currency: str,
        method: str,
        booking_id: int | None = None,
        order_id: int | None = None,
    ) -> datetime:
        """Take a payment. Sometimes the first card attempt fails and the customer retries."""
        if method == "card" and self.rng.random() < self.beh["payment_failure_rate"]:
            self._payment(ts, "charge", "failed", amount, currency, method, booking_id, order_id)
            ts += timedelta(seconds=self.rng.randint(30, 600))
        self._payment(ts, "charge", "succeeded", amount, currency, method, booking_id, order_id)
        return ts

    def _payment(self, ts, kind, status, amount, currency, method, booking_id=None, order_id=None) -> None:
        self.conn.execute(
            "insert into payments values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                self._next_id("payments"),
                booking_id,
                order_id,
                kind,
                method,
                status,
                currency,
                round(amount, 2),
                ts,
                ts,
                ts,
            ),
        )
        self._count(f"payments_{kind}_{status}")

    def _refund_all(self, ts: datetime, booking_id: int | None = None, order_id: int | None = None) -> None:
        """Pay back every successful charge on a booking or order."""
        column, value = ("booking_id", booking_id) if booking_id else ("order_id", order_id)
        charges = self.conn.execute(
            f"select amount, currency, method from payments where {column} = %s "
            "and payment_type = 'charge' and status = 'succeeded'",
            (value,),
        ).fetchall()
        for amount, currency, method in charges:
            self._payment(ts, "refund", "succeeded", float(amount), currency, method, booking_id, order_id)

    # ----------------------------------------------------------- bookings
    def _create_booking(self, ts: datetime) -> None:
        rng, beh = self.rng, self.beh
        cust = rng.choice(self.customers)
        booking_id = self._next_id("bookings")
        is_hotel = rng.random() < beh["hotel_share"]
        lead_days = min(330, int(rng.lognormvariate(math.log(beh["lead_time_median_days"]), 0.9)))
        start_date = (ts + timedelta(days=lead_days)).date()
        options = [d for d in reference.DESTINATIONS if d[0] != cust["airport"]]
        dest, _, nightly_eur = rng.choices(options, [d[1] for d in options])[0]
        travellers = rng.choices([1, 2, 3, 4], [25, 50, 12, 13])[0]

        if is_hotel:
            nights = min(14, 1 + int(rng.expovariate(1 / 2.5)))
            rooms = math.ceil(travellers / 2)
            amount_eur = nightly_eur * nights * rooms * rng.uniform(0.8, 1.25)
            origin = None
            hotel = f"{rng.choice(reference.HOTEL_WORDS[0])} {rng.choice(reference.HOTEL_WORDS[1])}"
        else:
            nights = rng.randint(3, 21)
            same_region = reference.AIRPORT_REGION[cust["airport"]] == reference.AIRPORT_REGION[dest]
            per_person = rng.uniform(60, 250) if same_region else rng.uniform(400, 1200)
            amount_eur = per_person * travellers
            origin, hotel = cust["airport"], None
        end_date = start_date + timedelta(days=nights)

        channel = rng.choices(["web", "mobile_app", "partner"], [50, 35, 15])[0]
        method = rng.choices(["card", "paypal", "pay_later"], [65, 20, 15])[0]
        currency = cust["currency"]
        amount = round(amount_eur * reference.PRICE_LIST_RATES[currency], 2)
        ref = f"TC-{booking_id:07d}"
        self.conn.execute(
            "insert into bookings values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "'confirmed', %s, null, %s, %s)",
            (
                booking_id,
                ref,
                cust["id"],
                "hotel" if is_hotel else "flight",
                origin,
                dest,
                hotel,
                start_date,
                end_date,
                travellers,
                channel,
                method,
                currency,
                amount,
                ts,
                ts,
                ts,
            ),
        )
        self._count("bookings_created")

        # Website session that ended in this booking.
        s = self._session(ts, cust["id"], channel)
        s.add("page_view", "/")
        s.add(
            "search",
            f"/search?destination={dest}",
            destination=dest,
            start_date=str(start_date),
            travellers=travellers,
            search_type="hotel" if is_hotel else "flight",
        )
        detail = f"/hotels/{dest}" if is_hotel else f"/flights/{origin}-{dest}"
        s.add("hotel_view" if is_hotel else "flight_view", detail, destination=dest)
        s.add("begin_checkout", "/checkout", value=amount, currency=currency)
        s.add("purchase", "/checkout/confirmation", booking_ref=ref, value=amount, currency=currency)
        s.end_at(ts)
        self._queue_session(s)

        # Payment: now, or a week before the trip for pay_later.
        if method == "pay_later":
            self._plan(
                max(ts, datetime.combine(start_date, time(9), UTC) - timedelta(days=7)), "charge_booking", booking_id
            )
        else:
            self._charge(ts, amount, currency, method, booking_id=booking_id)

        # Longer lead times and "pay later" cancel more often.
        base = beh["hotel_cancel_rate"] if is_hotel else beh["flight_cancel_rate"]
        p_cancel = base * (0.5 + lead_days / 60) * (1.8 if method == "pay_later" else 1.0)
        trip_start = datetime.combine(start_date, time(14), UTC)
        if rng.random() < min(p_cancel, 0.85) and trip_start > ts:
            self._plan(self._time_in(ts, trip_start), "cancel_booking", booking_id)
        else:
            self._plan(datetime.combine(end_date, time(11), UTC), "complete_booking", booking_id)

        # Cross-sell: some travellers buy luggage or an eSIM before they go.
        if rng.random() < beh["cross_sell_rate"] and trip_start > ts:
            window_start = max(ts, trip_start - timedelta(days=30))
            self._plan(self._time_in(window_start, trip_start), "cross_sell_order", cust["id"])

    def _booking(self, booking_id: int):
        return self.conn.execute(
            "select status, currency, total_amount, payment_method from bookings where booking_id = %s",
            (booking_id,),
        ).fetchone()

    def _on_charge_booking(self, ts: datetime, booking_id: int) -> None:
        status, currency, amount, _method = self._booking(booking_id)
        if status == "confirmed":
            self._charge(ts, float(amount), currency, "card", booking_id=booking_id)

    def _on_cancel_booking(self, ts: datetime, booking_id: int) -> None:
        self.conn.execute(
            "update bookings set status = 'cancelled', cancelled_at = %s, updated_at = %s where booking_id = %s",
            (ts, ts, booking_id),
        )
        self._refund_all(ts, booking_id=booking_id)
        self._count("bookings_cancelled")

    def _on_complete_booking(self, ts: datetime, booking_id: int) -> None:
        self.conn.execute(
            "update bookings set status = 'completed', updated_at = %s where booking_id = %s and status = 'confirmed'",
            (ts, booking_id),
        )
        self._count("bookings_completed")

    def _on_cross_sell_order(self, ts: datetime, customer_id: int) -> None:
        cust = next(c for c in self.customers if c["id"] == customer_id)
        self._create_order(ts, cust, travel_extras=True)

    # -------------------------------------------------------- shop orders
    def _create_order(self, ts: datetime, cust: dict, travel_extras: bool = False) -> None:
        rng = self.rng
        order_id = self._next_id("shop_orders")
        pool = self.products
        if travel_extras:  # trip shoppers lean towards eSIMs, insurance and adapters
            extras = [p for p in pool if p["category"] in ("eSIM & Connectivity", "Insurance", "Electronics")]
            pool = extras + pool
        lines = rng.choices([1, 2, 3, 4], [55, 28, 12, 5])[0]
        picked = list({p["id"]: p for p in rng.sample(pool, lines)}.values())
        currency = cust["currency"]
        channel = rng.choices(["web", "mobile_app"], [60, 40])[0]

        subtotal_eur, total = 0.0, 0.0
        items = []
        for line_no, product in enumerate(picked, start=1):
            qty = rng.choices([1, 2, 3], [85, 12, 3])[0]
            unit = reference.to_local_price(product["price_eur"], currency)
            discount = round(unit * qty * 0.1, 2) if rng.random() < 0.15 else 0.0
            items.append((order_id, line_no, product["id"], qty, unit, discount, ts, ts))
            subtotal_eur += product["price_eur"] * qty
            total += unit * qty - discount
        fee = 0.0 if subtotal_eur >= 50 else reference.to_local_price(5, currency)
        total += fee

        self.conn.execute(
            "insert into shop_orders values (%s, %s, %s, %s, %s, %s, 'placed', %s, %s, %s)",
            (order_id, cust["id"], channel, currency, fee, cust["country"], ts, ts, ts),
        )
        self.conn.cursor().executemany("insert into order_items values (%s, %s, %s, %s, %s, %s, %s, %s)", items)
        self._count("orders_created")
        self._count("order_items_created", len(items))

        s = self._session(ts, cust["id"], channel)
        s.add("page_view", "/shop")
        for product in picked:
            price = reference.to_local_price(product["price_eur"], currency)
            s.add("product_view", f"/shop/p/{product['sku']}", sku=product["sku"], price=price, currency=currency)
            s.add("add_to_cart", f"/shop/p/{product['sku']}", sku=product["sku"], price=price, currency=currency)
        s.add("view_cart", "/cart")
        s.add("begin_checkout", "/checkout", value=round(total, 2), currency=currency)
        s.add("purchase", "/checkout/confirmation", order_id=order_id, value=round(total, 2), currency=currency)
        s.end_at(ts)
        self._queue_session(s)

        self._charge(ts, total, currency, rng.choices(["card", "paypal"], [75, 25])[0], order_id=order_id)
        if rng.random() < self.beh["order_cancel_rate"]:
            self._plan(ts + timedelta(hours=rng.uniform(1, 10)), "cancel_order", order_id)
        else:
            self._plan(ts + timedelta(days=rng.uniform(0.5, 3)), "ship_order", order_id)

    def _set_order_status(self, ts: datetime, order_id: int, status: str) -> None:
        self.conn.execute(
            "update shop_orders set status = %s, updated_at = %s where order_id = %s", (status, ts, order_id)
        )
        self._count(f"orders_{status}")

    def _on_cancel_order(self, ts: datetime, order_id: int) -> None:
        self._set_order_status(ts, order_id, "cancelled")
        self._refund_all(ts, order_id=order_id)

    def _on_ship_order(self, ts: datetime, order_id: int) -> None:
        self._set_order_status(ts, order_id, "shipped")
        self._plan(ts + timedelta(days=self.rng.uniform(1, 6)), "deliver_order", order_id)

    def _on_deliver_order(self, ts: datetime, order_id: int) -> None:
        self._set_order_status(ts, order_id, "delivered")
        if self.rng.random() < self.beh["order_refund_rate"]:
            self._plan(ts + timedelta(days=self.rng.uniform(2, 20)), "refund_order", order_id)

    def _on_refund_order(self, ts: datetime, order_id: int) -> None:
        self._set_order_status(ts, order_id, "refunded")
        self._refund_all(ts, order_id=order_id)

    # --------------------------------------------------------- clickstream
    def _session(self, start: datetime, customer_id: int | None, channel: str = "web") -> clickstream.Session:
        if customer_id is not None:
            anonymous_id = f"anon-{customer_id * 7919 % 1_000_003:07d}"  # stable per customer
        else:
            anonymous_id = f"anon-{self.rng.getrandbits(40):012x}"
        return clickstream.Session(
            self.rng, start, customer_id, anonymous_id, channel, schema_drift=start >= self.drift_at
        )

    def _browse_session(self, ts: datetime) -> None:
        """A visit that ends before buying, at a random step of the funnel."""
        rng = self.rng
        logged_in = rng.random() < 0.35
        s = self._session(ts, rng.choice(self.customers)["id"] if logged_in else None)
        s.add("page_view", rng.choice(["/", "/", "/shop", "/deals"]))
        if rng.random() < 0.5:  # trip shopper
            dest = rng.choices(reference.DESTINATIONS, [d[1] for d in reference.DESTINATIONS])[0][0]
            kind = rng.choice(["hotel", "hotel", "flight"])
            if rng.random() < 0.75:
                s.add("search", f"/search?destination={dest}", destination=dest, search_type=kind)
                if rng.random() < 0.55:
                    s.add(f"{kind}_view", f"/{kind}s/{dest}", destination=dest)
                    if rng.random() < 0.2:
                        s.add("begin_checkout", "/checkout", currency="EUR")
        elif rng.random() < 0.7:  # shop browser
            product = rng.choice(self.products)
            price = round(product["price_eur"], 2)
            s.add("product_view", f"/shop/p/{product['sku']}", sku=product["sku"], price=price, currency="EUR")
            if rng.random() < 0.3:
                s.add("add_to_cart", f"/shop/p/{product['sku']}", sku=product["sku"], price=price, currency="EUR")
                if rng.random() < 0.4:
                    s.add("view_cart", "/cart")
                    if rng.random() < 0.3:
                        s.add("begin_checkout", "/checkout", currency="EUR")
        self._queue_session(s)

    def _queue_session(self, s: clickstream.Session) -> None:
        self._events += clickstream.schedule_arrivals(
            self.rng, s.events, self.mess["late_event_rate"], self.mess["duplicate_event_rate"]
        )
        self._count("events_generated", len(s.events))

    def _upload_arrived_events(self, until: datetime) -> None:
        """Upload every event that has 'arrived' by now; late ones wait for a later run.

        The rows are deleted and the files uploaded in one transaction. If an
        upload fails, the delete rolls back and the next run tries again.
        """
        with self.conn.transaction():
            rows = self.conn.execute(
                "delete from _sim_pending_events where arrival_at < %s returning arrival_at, event", (until,)
            ).fetchall()
            if rows:
                self._count("files_written", clickstream.upload_files(self.s3, self.bucket, rows, until))
                self._count("events_uploaded", len(rows))
