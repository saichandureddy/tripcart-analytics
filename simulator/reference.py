"""Fixed reference data the simulator draws from: countries, airports, prices.

The full airport list (OpenFlights) becomes a dbt seed in milestone 3. The
simulator only needs a few dozen popular airports to pick trips from.
"""

import csv
from pathlib import Path

# Where customers live. weight = share of customers.
COUNTRIES = {
    "US": {"weight": 28, "currency": "USD", "locale": "en_US", "airports": ["JFK", "LAX", "ORD", "SFO", "MIA"]},
    "GB": {"weight": 20, "currency": "GBP", "locale": "en_GB", "airports": ["LHR", "LGW", "MAN", "EDI"]},
    "DE": {"weight": 15, "currency": "EUR", "locale": "de_DE", "airports": ["FRA", "MUC", "BER"]},
    "IN": {"weight": 12, "currency": "INR", "locale": "en_IN", "airports": ["DEL", "BOM", "BLR"]},
    "FR": {"weight": 10, "currency": "EUR", "locale": "fr_FR", "airports": ["CDG", "ORY", "NCE"]},
    "ES": {"weight": 9, "currency": "EUR", "locale": "es_ES", "airports": ["MAD", "BCN"]},
    "NL": {"weight": 6, "currency": "EUR", "locale": "nl_NL", "airports": ["AMS"]},
}

# Region of every airport used above or below, for flight pricing.
AIRPORT_REGION = {
    "JFK": "north_america",
    "LAX": "north_america",
    "ORD": "north_america",
    "SFO": "north_america",
    "MIA": "north_america",
    "HNL": "north_america",
    "CUN": "latin_america",
    "LHR": "europe",
    "LGW": "europe",
    "MAN": "europe",
    "EDI": "europe",
    "FRA": "europe",
    "MUC": "europe",
    "BER": "europe",
    "CDG": "europe",
    "ORY": "europe",
    "NCE": "europe",
    "MAD": "europe",
    "BCN": "europe",
    "AMS": "europe",
    "LIS": "europe",
    "FCO": "europe",
    "ATH": "europe",
    "PMI": "europe",
    "DBV": "europe",
    "PRG": "europe",
    "VIE": "europe",
    "KEF": "europe",
    "IST": "europe",
    "DEL": "asia",
    "BOM": "asia",
    "BLR": "asia",
    "GOI": "asia",
    "BKK": "asia",
    "DPS": "asia",
    "SIN": "asia",
    "NRT": "asia",
    "MLE": "asia",
    "DXB": "middle_east",
    "CPT": "africa",
    "RAK": "africa",
    "SYD": "oceania",
}

# Where people travel to: (airport, popularity weight, typical hotel night in EUR).
DESTINATIONS = [
    ("BCN", 10, 140),
    ("LIS", 8, 120),
    ("CDG", 9, 190),
    ("FCO", 8, 160),
    ("AMS", 6, 180),
    ("ATH", 5, 110),
    ("PMI", 6, 150),
    ("DBV", 3, 170),
    ("PRG", 4, 95),
    ("VIE", 3, 130),
    ("KEF", 2, 210),
    ("IST", 4, 100),
    ("LHR", 7, 220),
    ("NCE", 3, 200),
    ("JFK", 7, 260),
    ("LAX", 4, 230),
    ("MIA", 3, 210),
    ("HNL", 2, 280),
    ("CUN", 4, 160),
    ("DXB", 5, 170),
    ("BKK", 5, 80),
    ("DPS", 4, 90),
    ("SIN", 3, 200),
    ("NRT", 4, 170),
    ("MLE", 2, 380),
    ("GOI", 3, 70),
    ("DEL", 2, 75),
    ("CPT", 2, 120),
    ("RAK", 3, 95),
    ("SYD", 2, 210),
]

HOTEL_WORDS = (
    ["Grand", "Royal", "Harbour", "Old Town", "Park", "Riverside", "Sunset", "Central", "Palm", "Garden"],
    ["Hotel", "Suites", "Resort", "Inn", "Lodge", "Boutique Hotel", "Apartments"],
)

# Rough EUR exchange rates, only used to turn EUR list prices into local
# prices. Real daily rates come from the FX loader and are applied in dbt.
PRICE_LIST_RATES = {"EUR": 1.0, "USD": 1.08, "GBP": 0.85, "INR": 90.0}

# Booking seasonality: relative demand by travel month (1 = January).
MONTH_DEMAND = {1: 0.8, 2: 0.8, 3: 0.9, 4: 1.0, 5: 1.1, 6: 1.3, 7: 1.5, 8: 1.5, 9: 1.1, 10: 0.9, 11: 0.8, 12: 1.2}

# Shop demand by weekday (0 = Monday): people shop more at the weekend.
WEEKDAY_DEMAND = {0: 0.9, 1: 0.85, 2: 0.9, 3: 0.95, 4: 1.05, 5: 1.2, 6: 1.15}


def load_products() -> list[dict]:
    """Read the product catalog that ships with the simulator."""
    path = Path(__file__).parent / "reference" / "products.csv"
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def to_local_price(price_eur: float, currency: str) -> float:
    """Convert a EUR price to the customer's currency and round it like a shop would."""
    local = price_eur * PRICE_LIST_RATES[currency]
    if currency == "INR":
        return float(round(local, -1) - 1)  # 2,690 style prices
    return round(round(local) - 0.01, 2)  # 34.99 style prices
