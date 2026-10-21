"""Category-specific listing attributes.

One definition, three consumers: the sell form renders these fields, the
query parser learns what words to look for, and search builds SQL from
them. Keeping it in a single place is what stops the three drifting apart —
a field added here appears in all three without further work.

Field shape:
    key         column name in product_attributes
    label       shown on the sell form
    type        number | select | text
    unit        appended in the UI, and understood by the parser
    options     for select
    searchable  parser may extract it from a free-text query
    aliases     other words a user might type for this attribute
"""

from datetime import datetime

# Values users type that mean the same thing, normalised on the way in so
# "Grey" and "gray" don't become two different filters.
COLOR_OPTIONS = [
    "Black", "White", "Grey", "Silver", "Blue", "Red", "Green",
    "Brown", "Gold", "Yellow", "Orange", "Purple", "Pink", "Beige",
]
COLOR_ALIASES = {"gray": "Grey", "grey": "Grey", "silver": "Silver",
                 "golden": "Gold", "maroon": "Red", "navy": "Blue"}

FUEL_OPTIONS = ["Petrol", "Diesel", "CNG", "Electric", "Hybrid", "LPG"]
TRANSMISSION_OPTIONS = ["Manual", "Automatic"]
STORAGE_OPTIONS = ["16 GB", "32 GB", "64 GB", "128 GB", "256 GB", "512 GB", "1 TB"]
RAM_OPTIONS = ["2 GB", "3 GB", "4 GB", "6 GB", "8 GB", "12 GB", "16 GB"]
FURNISHING_OPTIONS = ["Unfurnished", "Semi-furnished", "Fully furnished"]

# min 1950: vintage and classic vehicles are a real category on second-hand
# marketplaces, and 1990 rejected them outright. max is computed rather than
# hardcoded — a fixed 2026 would start rejecting current-year listings the
# moment the year rolls over, with nobody remembering why. +1 because
# next-year models are advertised before the year begins.
_YEAR = {
    "key": "year", "label": "Year of manufacture", "type": "number",
    "min": 1950, "max": datetime.now().year + 1, "placeholder": "e.g. 2018",
    "searchable": True, "aliases": ["year", "model", "make"],
}
_KM = {
    "key": "km_driven", "label": "Kilometres driven", "type": "number",
    "unit": "km", "min": 0, "max": 1000000, "placeholder": "e.g. 45000",
    "searchable": True, "aliases": ["km", "kms", "kilometre", "kilometer",
                                    "kilometres", "kilometers", "driven", "run", "odo"],
}
_COLOR = {
    "key": "color", "label": "Colour", "type": "select", "options": COLOR_OPTIONS,
    "searchable": True, "aliases": ["colour", "color"],
}
_OWNERS = {
    "key": "owners", "label": "Number of owners", "type": "number",
    "min": 1, "max": 10, "placeholder": "e.g. 1",
    "searchable": True, "aliases": ["owner", "owners", "hand"],
}

CATEGORY_ATTRIBUTES = {
    "cars": [
        _YEAR, _KM,
        {"key": "fuel", "label": "Fuel type", "type": "select",
         "options": FUEL_OPTIONS, "searchable": True, "aliases": ["fuel"]},
        {"key": "transmission", "label": "Transmission", "type": "select",
         "options": TRANSMISSION_OPTIONS, "searchable": True,
         "aliases": ["transmission", "gearbox", "auto", "manual"]},
        _COLOR, _OWNERS,
    ],
    "bikes": [
        _YEAR, _KM,
        {"key": "engine_cc", "label": "Engine (cc)", "type": "number",
         "unit": "cc", "min": 50, "max": 2000, "placeholder": "e.g. 150",
         "searchable": True, "aliases": ["cc", "engine"]},
        _COLOR, _OWNERS,
    ],
    "phones": [
        _COLOR,
        {"key": "storage", "label": "Storage", "type": "select",
         "options": STORAGE_OPTIONS, "searchable": True,
         "aliases": ["storage", "gb", "tb", "memory", "rom"]},
        {"key": "ram", "label": "RAM", "type": "select", "options": RAM_OPTIONS,
         "searchable": True, "aliases": ["ram"]},
        {"key": "year", "label": "Year of purchase", "type": "number",
         "min": 2010, "max": 2026, "placeholder": "e.g. 2022",
         "searchable": True, "aliases": ["year", "bought", "purchased"]},
    ],
    "electronics": [
        _COLOR,
        {"key": "year", "label": "Year of purchase", "type": "number",
         "min": 2005, "max": 2026, "placeholder": "e.g. 2021",
         "searchable": True, "aliases": ["year", "bought", "purchased"]},
        {"key": "warranty", "label": "Warranty remaining", "type": "select",
         "options": ["None", "Under 6 months", "6-12 months", "Over 1 year"],
         "searchable": False},
    ],
    "furniture": [
        _COLOR,
        {"key": "material", "label": "Material", "type": "select",
         "options": ["Wood", "Metal", "Plastic", "Glass", "Fabric", "Leather"],
         "searchable": True, "aliases": ["material", "made of"]},
    ],
    "appliances": [
        _COLOR,
        {"key": "capacity", "label": "Capacity", "type": "text",
         "placeholder": "e.g. 250 L, 7 kg", "searchable": False},
        {"key": "year", "label": "Year of purchase", "type": "number",
         "min": 2005, "max": 2026, "placeholder": "e.g. 2021",
         "searchable": True, "aliases": ["year", "bought", "purchased"]},
    ],
    "property": [
        {"key": "bedrooms", "label": "Bedrooms (BHK)", "type": "number",
         "min": 1, "max": 10, "placeholder": "e.g. 2",
         "searchable": True, "aliases": ["bhk", "bedroom", "bedrooms", "bed"]},
        {"key": "area_sqft", "label": "Area (sq ft)", "type": "number",
         "unit": "sq ft", "min": 100, "max": 100000, "placeholder": "e.g. 1200",
         "searchable": True, "aliases": ["sqft", "sq ft", "square feet", "area"]},
        {"key": "furnishing", "label": "Furnishing", "type": "select",
         "options": FURNISHING_OPTIONS, "searchable": True, "aliases": ["furnished", "furnishing"]},
    ],
    "fashion": [
        _COLOR,
        {"key": "size", "label": "Size", "type": "text",
         "placeholder": "e.g. M, 42, UK 9", "searchable": True, "aliases": ["size"]},
    ],
}


def attributes_for(category):
    return CATEGORY_ATTRIBUTES.get(category, [])


def searchable_attributes():
    """Every attribute the parser may extract, deduplicated across
    categories — `year` appears under several and must be one entry."""
    seen, out = set(), []
    for attrs in CATEGORY_ATTRIBUTES.values():
        for a in attrs:
            if a.get("searchable") and a["key"] not in seen:
                seen.add(a["key"])
                out.append(a)
    return out


def normalise_value(key, value):
    """Canonical form for a stored value, so filters match reliably."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if key == "color":
        return COLOR_ALIASES.get(text.lower(), text.title())
    if key in ("fuel", "transmission", "furnishing", "material"):
        return text.title()
    if key in ("storage", "ram"):
        # "128gb" and "128 GB" must land on the same option.
        t = text.upper().replace(" ", "")
        for opt in STORAGE_OPTIONS + RAM_OPTIONS:
            if opt.upper().replace(" ", "") == t:
                return opt
        return text
    return text


def numeric_value(key, value):
    """The comparable number for an attribute, or None if it isn't numeric.
    Stored alongside the text so range filters stay indexed."""
    if value is None:
        return None
    import re
    m = re.search(r"[\d.]+", str(value).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None
