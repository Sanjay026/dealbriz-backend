"""
Search matching for the marketplace.

Two problems this solves:

1. Spacing and punctuation. Sellers write "Smart TV", "SmartTV" and
   "Smart-TV" for the same thing. A plain LIKE on the raw text matches
   only one of them. We therefore compare against a *normalised* form of
   the title with spaces, hyphens and dots removed, so all three collapse
   to "smarttv" and match each other.

2. Vocabulary. A buyer searching "laptop" should also see listings titled
   "MacBook" or "notebook"; someone searching "fridge" should see
   "refrigerator". SYNONYMS below maps a search term to the other words
   that mean the same thing in this marketplace.

Deliberately NOT done here: fuzzy/edit-distance matching. It sounds
appealing but at small catalogue sizes it mostly produces confusing
false positives ("iphone" matching "ip camera"), and it can't use an
index. Prefix + substring + synonyms covers the real cases.
"""
import re

# Search term -> other words that should also match it.
# Kept bidirectional at lookup time: searching either side finds the other.
SYNONYM_GROUPS = [
    # electronics
    ["laptop", "notebook", "macbook", "ultrabook", "chromebook"],
    ["tv", "television", "smarttv", "led tv", "lcd tv", "qled", "oled"],
    ["phone", "mobile", "smartphone", "handset", "cellphone"],
    ["earphones", "earbuds", "headphones", "headset", "airpods", "tws"],
    ["speaker", "soundbar", "bluetooth speaker"],
    ["camera", "dslr", "mirrorless", "camcorder"],
    ["computer", "desktop", "pc", "cpu", "monitor"],
    ["tablet", "ipad", "tab"],
    ["console", "playstation", "ps4", "ps5", "xbox", "nintendo"],
    ["smartwatch", "watch", "fitness band", "smart band"],
    ["printer", "scanner"],
    ["router", "wifi router", "modem"],
    # appliances
    ["fridge", "refrigerator"],
    ["ac", "air conditioner", "airconditioner", "split ac", "window ac"],
    ["washing machine", "washer"],
    ["microwave", "oven", "otg"],
    ["cooler", "air cooler"],
    ["geyser", "water heater"],
    ["mixer", "grinder", "mixer grinder", "blender"],
    ["purifier", "water purifier", "ro"],
    ["dishwasher"],
    ["fan", "ceiling fan", "table fan"],
    ["vacuum", "vacuum cleaner"],
    # vehicles
    ["car", "sedan", "hatchback", "suv"],
    ["bike", "motorcycle", "motorbike", "two wheeler"],
    ["scooter", "scooty", "activa", "moped"],
    ["cycle", "bicycle"],
    ["ev", "electric vehicle", "electric scooter", "electric bike"],
    # furniture
    ["sofa", "couch", "settee"],
    ["bed", "cot", "mattress"],
    ["almirah", "wardrobe", "cupboard", "closet"],
    ["table", "desk", "study table", "dining table"],
    ["chair", "recliner", "stool"],
    ["shelf", "rack", "bookshelf", "shoe rack"],
    # fashion
    ["shoes", "sneakers", "footwear", "trainers"],
    ["jeans", "denim", "trousers", "pants"],
    ["jacket", "coat", "blazer"],
    ["bag", "backpack", "rucksack", "handbag"],
    ["saree", "sari"],
    # property
    ["flat", "apartment", "bhk"],
    ["house", "villa", "bungalow", "independent house"],
    ["plot", "land"],
    ["shop", "showroom", "commercial space"],
    ["office", "office space"],
]

# Built once: term -> set of all terms in any group containing it.
_SYNONYM_INDEX = {}
for _group in SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYM_INDEX.setdefault(_word, set()).update(_group)


def normalise(text):
    """Lowercase and strip everything that isn't a letter or digit, so
    'Smart-TV', 'smart tv' and 'SmartTV' all become 'smarttv'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Terms this short only make sense as a whole search ("ac", "tv", "pc").
# As substrings they appear inside dozens of unrelated words — "ac" alone
# matches MacBook, Activa and washing machine — so they are only ever used
# when the user typed exactly that, never via word-splitting or synonyms.
_SHORT_TERM_MAX = 3


def expand_terms(query):
    """Turn a raw search string into the set of normalised forms to match.

    Includes the whole phrase, each individual word, and their synonyms.
    Two rules keep this from over-matching:
      - fragments under 2 characters are dropped entirely;
      - very short terms (<= 3 chars, e.g. "ac", "tv") are only kept when
        the user typed exactly that as the whole query, because as
        substrings they match inside unrelated words.
    """
    raw = (query or "").strip().lower()
    if not raw:
        return []

    whole = normalise(raw)
    candidates = {raw}
    words = [w for w in re.split(r"[\s\-_/,]+", raw) if w]
    candidates.update(words)

    # Multi-word synonym keys ("washing machine") need the full phrase,
    # single-word keys need the individual words — check both.
    for key in list(candidates):
        for syn in _SYNONYM_INDEX.get(key, ()):
            candidates.add(syn)

    out = set()
    for c in candidates:
        n = normalise(c)
        if len(n) < 2:
            continue
        # A short term survives only if it IS the search, not a fragment of it.
        if len(n) <= _SHORT_TERM_MAX and n != whole:
            continue
        out.add(n)

    # If filtering left nothing (e.g. the user searched "ac" and it was
    # dropped by a rule change), fall back to the literal query.
    if not out and len(whole) >= 2:
        out.add(whole)
    return sorted(out, key=len, reverse=True)


def is_short_term(term):
    """A term short enough that substring matching is unsafe — it must match
    as a whole word instead. "ac" as a substring hits MacBook and Activa;
    as a word it correctly hits only "split AC"."""
    return len(term) <= _SHORT_TERM_MAX
