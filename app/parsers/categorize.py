"""
Rule-based transaction categorizer.

Approach: keyword matching against the transaction description, checked in
order against a category->keywords dict. First match wins. This is simple,
free (no API calls), transparent (you can see exactly why something got
categorized a certain way), and fully editable -- just add keywords below.

Anything that doesn't match becomes "Uncategorized" so nothing is silently
mis-filed; the dashboard surfaces these so you can add rules for them.
"""

import re
import pandas as pd

# Order matters: more specific categories should come before generic ones.
CATEGORY_RULES = {
    "Income/Payments": [
        "payment thank you", "autopay", "payroll", "direct dep", "deposit",
        "payment received", "online payment", "mobile payment",
    ],
    "Subscriptions & Streaming": [
        "netflix", "hulu", "disney+", "disney plus", "spotify", "apple.com/bill",
        "apple music", "youtube premium", "hbo max", "max.com", "paramount+",
        "peacock", "amazon prime", "prime video", "audible", "icloud",
        "google storage", "google one", "dropbox", "adobe", "nyt", "new york times",
        "sling tv", "playstation network", "xbox", "nintendo",
    ],
    "Groceries": [
        "safeway", "kroger", "trader joe", "whole foods", "wegmans", "publix",
        "albertsons", "vons", "ralphs", "aldi", "sprouts", "costco wholesale",
        "walmart grocery", "grocery", "central market", "wholefds", "wild fork",
    ],
    "Dining & Coffee": [
        "starbucks", "peet's", "peets coffee", "dunkin", "mcdonald", "chipotle",
        "doordash", "uber eats", "grubhub", "postmates", "restaurant", "cafe",
        "coffee", "pizza", "taco", "sushi", "bar & grill", "diner", "bistro",
        "burger", "deli",
        # POS-system prefixes that are overwhelmingly restaurants/cafes even
        # though the merchant name itself varies (each is its own business):
        "tst*", "tst *",
        "eatzi", "sweetgreen", "flower child",
    ],
    "Transportation": [
        "uber", "lyft", "shell oil", "chevron", "exxon", "76 -", "arco",
        "gas station", "parking", "toll", "dmv", "tx dps", "metro transit", "bart",
        "caltrain", "auto repair", "jiffy lube", "valvoline",
    ],
    "Travel": [
        "airlines", "airbnb", "expedia", "booking.com", "marriott", "hilton",
        "hyatt", "delta air", "united air", "southwest", "alaska air",
        "hotel", "rental car", "avis", "hertz", "enterprise rent",
        "american0",  # AA ticket charges post as "AMERICAN0012345678 PHOENIX AZ"
    ],
    "Shopping": [
        "amazon", "amzn", "target", "walmart", "ebay", "best buy", "ikea",
        "home depot", "lowe's", "lowes", "tj maxx", "marshalls", "nordstrom",
        "macy's", "etsy", "wayfair", "costco.com",
    ],
    "Utilities & Bills": [
        "electric", "pg&e", "pge", "water dept", "sewer", "comcast", "xfinity",
        "at&t", "att bill", "verizon", "t-mobile", "internet", "cable bill",
        "waste management", "trash service",
    ],
    "Health & Fitness": [
        "pharmacy", "cvs", "walgreens", "rite aid", "gym", "fitness",
        "planet fitness", "equinox", "24 hour fitness", "yoga", "urgent care",
        "medical", "dental", "clinic", "hospital", "doctor",
        "counseling", "therapy", "vitamin shoppe", "vitaminshoppe",
    ],
    "Insurance": [
        "insurance", "geico", "state farm", "progressive", "allstate",
        "farmers ins",
    ],
    "Entertainment": [
        "movie", "cinema", "amc ", "regal cinemas", "ticketmaster", "steam games",
        "concert", "museum", "bowling", "golf",
    ],
    "Personal Care": [
        "salon", "barber", "spa", "nails", "sephora", "ulta", "floyd's 99",
        "floyd s 99",
    ],
    "Fees & Interest": [
        "interest charge", "late fee", "annual fee", "overdraft", "service fee",
        "atm fee", "foreign transaction fee",
    ],
    "Housing": [
        "mortgage", "rent payment", "hoa ", "property mgmt", "landlord",
    ],
}

# Pre-compile for speed
_COMPILED_RULES = {
    cat: [re.compile(re.escape(kw), re.IGNORECASE) for kw in kws]
    for cat, kws in CATEGORY_RULES.items()
}


def categorize_description(description, overrides=None):
    """overrides: optional dict of {description: category} from the user's
    own manual corrections -- checked first so a manual fix always wins."""
    if overrides and description in overrides:
        return overrides[description]
    for category, patterns in _COMPILED_RULES.items():
        for pattern in patterns:
            if pattern.search(description):
                return category
    return "Uncategorized"


# Categories that are inherently irregular/occasional rather than everyday --
# used to flag "this isn't a monthly habit" spend (a trip, a one-off repair)
# separately from consistent day-to-day categories like Groceries or Dining.
IRREGULAR_CATEGORIES = {"Travel", "Shopping", "Entertainment", "Fees & Interest"}

ALL_CATEGORIES = list(CATEGORY_RULES.keys()) + ["Uncategorized"]


def categorize_dataframe(df, description_col="description"):
    df = df.copy()
    df["category"] = df[description_col].apply(categorize_description)
    return df


def uncategorized_summary(df):
    """Helps you find which merchants need a new rule added above."""
    uncat = df[df["category"] == "Uncategorized"]
    return (
        uncat.groupby("description")["amount"]
        .agg(["count", "sum"])
        .sort_values("count", ascending=False)
    )
