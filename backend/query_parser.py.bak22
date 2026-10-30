"""Turn a natural-language search into structured filters.

    "hyundai car 2018 model under 5 lac and below 1 lac km run"
      -> brand=Hyundai, category=cars, year=2018,
         price<=500000, km_driven<=100000

Rule-based on purpose. These queries are almost entirely *numeric
constraints* ("under 5 lac", "below 1 lakh km"), and that is precisely what
embeddings cannot represent — "under 5 lac" and "under 6 lac" are nearly
identical vectors. A parser gets them exactly right, runs in microseconds,
costs nothing per query, and when it misreads something you can see why.

The vocabulary for brands and models is read from the listings themselves,
so it stays current without anyone maintaining a word list.
"""
import re

from attributes import COLOR_ALIASES, COLOR_OPTIONS, searchable_attributes

# ---------------------------------------------------------------------
# Number handling
# ---------------------------------------------------------------------
# Indian numbering is what users actually type: "5 lac", "1.5 lakh", "50k".
MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000, "hazar": 1_000, "hajar": 1_000,
    "l": 100_000, "lac": 100_000, "lacs": 100_000, "lakh": 100_000,
    "lakhs": 100_000, "lak": 100_000,
    "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
}

# Words that turn a number into a bound, and in which direction.
UPPER_BOUND = ["under", "below", "less than", "lesser than", "upto", "up to",
               "within", "max", "maximum", "not more than", "cheaper than",
               "at most", "or less", "budget"]
LOWER_BOUND = ["above", "over", "more than", "greater than", "at least",
               "minimum", "min", "starting from", "from"]
RANGE_WORDS = ["between", "in the range", "range of"]

CONDITION_WORDS = {
    "new": "new", "brand new": "new", "unused": "new",
    "like new": "excellent", "excellent": "excellent", "mint": "excellent",
    "good": "good", "fair": "fair", "used": None, "second hand": None,
}

# Reuse the category vocabulary the chatbot already uses so search and chat
# agree on what "mobiles" means.
CATEGORY_WORDS = {
    "cars": {"car", "cars", "sedan", "hatchback", "suv", "vehicle"},
    "phones": {"phone", "phones", "mobile", "mobiles", "smartphone", "iphone", "android"},
    "bikes": {"bike", "bikes", "motorcycle", "scooter", "scooty", "bullet"},
    "electronics": {"electronics", "laptop", "laptops", "tv", "television",
                    "camera", "computer", "console", "tablet", "ipad"},
    "furniture": {"furniture", "sofa", "bed", "table", "chair", "wardrobe", "almirah"},
    "appliances": {"appliance", "appliances", "fridge", "refrigerator",
                   "washing", "ac", "microwave", "cooler", "geyser"},
    "fashion": {"fashion", "clothes", "clothing", "shoes", "watch", "bag"},
    "property": {"property", "flat", "house", "apartment", "plot", "land", "shop", "bhk"},
}

STOPWORDS = {"a", "an", "the", "in", "for", "with", "and", "or", "of", "is",
             "are", "me", "show", "find", "want", "need", "looking", "search",
             "any", "some", "please", "i", "my", "that", "this", "it", "run",
             "model", "condition", "only", "near"}


def _to_number(raw, unit=None):
    """'5' + 'lac' -> 500000.  '50k' -> 50000.  '1,20,000' -> 120000."""
    try:
        value = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if unit:
        value *= MULTIPLIERS.get(unit.lower(), 1)
    return value


class ParsedQuery:
    def __init__(self):
        self.text = ""
        self.category = None
        self.brand = None
        self.model = None
        self.condition = None
        self.price_min = None
        self.price_max = None
        self.attributes = {}      # key -> {"eq"|"min"|"max": value}
        self.keywords = []        # leftover words for title matching
        self.matched = []         # human-readable trace, shown as filter chips

    def to_dict(self):
        return {
            "text": self.text, "category": self.category, "brand": self.brand,
            "model": self.model, "condition": self.condition,
            "price_min": self.price_min, "price_max": self.price_max,
            "attributes": self.attributes, "keywords": self.keywords,
            "matched": self.matched,
        }

    def is_empty(self):
        return not any([self.category, self.brand, self.model, self.condition,
                        self.price_min, self.price_max, self.attributes, self.keywords])


def _find_bound(text, position):
    """Which comparator governs the number at `position`?

    Looks backwards a short way rather than anywhere in the string: in
    "under 5 lac and below 1 lac km", the word "below" must bind to the km
    figure, not to the price.
    """
    window = text[max(0, position - 28):position]
    best, best_at = None, -1
    for word in UPPER_BOUND:
        at = window.rfind(word)
        if at > best_at:
            best, best_at = "max", at
    for word in LOWER_BOUND:
        at = window.rfind(word)
        if at > best_at:
            best, best_at = "min", at
    return best


def parse_query(raw, brands=None, models=None):
    """`brands` and `models` come from the live listings, so the parser
    recognises whatever is actually for sale."""
    q = ParsedQuery()
    q.text = (raw or "").strip()
    if not q.text:
        return q

    text = " " + re.sub(r"\s+", " ", q.text.lower()).strip() + " "
    consumed = set()          # character spans already claimed by a filter

    def claim(start, end):
        consumed.update(range(start, end))

    def is_free(start, end):
        return not any(i in consumed for i in range(start, end))

    # ---- 1. Ranges: "between 20000 and 50000" ----------------------
    for m in re.finditer(
        r"(?:between|from)\s+(?:rs\.?|₹)?\s*([\d,.]+)\s*(k|l|lac|lacs|lakh|lakhs|cr|crore|thousand)?\s*"
        r"(?:to|-|and)\s+(?:rs\.?|₹)?\s*([\d,.]+)\s*(k|l|lac|lacs|lakh|lakhs|cr|crore|thousand)?", text):
        lo = _to_number(m.group(1), m.group(2))
        hi = _to_number(m.group(3), m.group(4) or m.group(2))
        if lo and hi:
            q.price_min, q.price_max = min(lo, hi), max(lo, hi)
            q.matched.append(f"₹{int(q.price_min):,} – ₹{int(q.price_max):,}")
            claim(m.start(), m.end())

    # ---- 2. Attribute numbers, before price ------------------------
    # A number followed by a unit ("1 lac km", "150 cc") belongs to that
    # attribute. Doing this first stops "1 lac km" being read as a budget.
    attr_defs = searchable_attributes()
    unit_pattern = "|".join(sorted(
        (re.escape(a) for d in attr_defs for a in d.get("aliases", [])),
        key=len, reverse=True))

    for m in re.finditer(
        r"([\d,.]+)\s*(k|l|lac|lacs|lakh|lakhs|cr|crore|thousand)?\s*(" + unit_pattern + r")\b", text):
        if not is_free(m.start(), m.end()):
            continue
        word = m.group(3)
        target = next((d for d in attr_defs if word in d.get("aliases", [])), None)
        if not target or target["type"] != "number":
            continue
        value = _to_number(m.group(1), m.group(2))
        if value is None:
            continue
        bound = _find_bound(text, m.start()) or "eq"
        q.attributes.setdefault(target["key"], {})[bound] = value
        label = target["label"]
        q.matched.append(
            f"{label} {'≤' if bound=='max' else '≥' if bound=='min' else '='} {int(value):,}")
        claim(m.start(), m.end())

    # ---- 3. Year: a bare 4-digit number in a plausible range -------
    for m in re.finditer(r"\b(19[89]\d|20[0-2]\d)\b", text):
        if not is_free(m.start(), m.end()):
            continue
        year = int(m.group(1))
        bound = _find_bound(text, m.start())
        q.attributes.setdefault("year", {})[bound or "eq"] = year
        q.matched.append(f"Year {'≤' if bound=='max' else '≥' if bound=='min' else ''} {year}".strip())
        claim(m.start(), m.end())

    # ---- 4. Price ---------------------------------------------------
    if q.price_min is None and q.price_max is None:
        for m in re.finditer(
            r"(?:rs\.?|₹|inr)?\s*([\d,.]+)\s*(k|l|lac|lacs|lakh|lakhs|cr|crore|thousand)?\b", text):
            if not is_free(m.start(), m.end()):
                continue
            value = _to_number(m.group(1), m.group(2))
            if value is None or value < 100:
                continue        # too small to be a price; likely a size or count
            bound = _find_bound(text, m.start())
            if bound == "max":
                q.price_max = value
                q.matched.append(f"Under ₹{int(value):,}")
            elif bound == "min":
                q.price_min = value
                q.matched.append(f"Over ₹{int(value):,}")
            else:
                # A bare large number with a currency marker is a budget;
                # without one it's ambiguous, so treat it as a ceiling only
                # when it's clearly money-shaped.
                if m.group(2) or re.search(r"(rs|₹|inr)", text[max(0, m.start()-5):m.start()+2]):
                    q.price_max = value
                    q.matched.append(f"Under ₹{int(value):,}")
                else:
                    continue
            claim(m.start(), m.end())
            break

    # ---- 5. Brand and model, from live listing vocabulary -------------
    # Claimed BEFORE the category, because a model name often contains the
    # category word: "iPhone 12" would otherwise lose "iphone" to the
    # phones category and never match as a model. Longest names first so
    # "Royal Enfield" wins over "Royal".
    for name in sorted(models or [], key=len, reverse=True):
        m = re.search(r"\b" + re.escape(name.lower()) + r"\b", text)
        if m and is_free(m.start(), m.end()):
            q.model = name
            q.matched.append(name)
            claim(m.start(), m.end())
            break
    for name in sorted(brands or [], key=len, reverse=True):
        m = re.search(r"\b" + re.escape(name.lower()) + r"\b", text)
        if m and is_free(m.start(), m.end()):
            q.brand = name
            q.matched.append(name)
            claim(m.start(), m.end())
            break
    else:
        # Multi-word brands are rarely typed in full: people search "maruti
        # swift", not "maruti suzuki swift". Fall back to matching any
        # distinctive word of the brand name.
        for name in sorted(brands or [], key=len, reverse=True):
            parts = [p for p in name.lower().split() if len(p) > 3]
            if len(parts) < 2:
                continue
            for part in parts:
                m = re.search(r"\b" + re.escape(part) + r"\b", text)
                if m and is_free(m.start(), m.end()):
                    q.brand = name
                    q.matched.append(name)
                    claim(m.start(), m.end())
                    break
            if q.brand:
                break

    # ---- 6. Category -------------------------------------------------
    words = set(re.findall(r"[a-z]+", text))
    for slug, terms in CATEGORY_WORDS.items():
        if words & terms:
            q.category = slug
            q.matched.append(slug.title())
            for t in terms & words:
                for m in re.finditer(r"\b" + re.escape(t) + r"\b", text):
                    claim(m.start(), m.end())
            break

    # ---- 7. Colour and other select attributes -----------------------
    for m in re.finditer(r"\b([a-z]+)\b", text):
        w = m.group(1)
        if not is_free(m.start(), m.end()):
            continue
        canon = COLOR_ALIASES.get(w) or (w.title() if w.title() in COLOR_OPTIONS else None)
        if canon:
            q.attributes.setdefault("color", {})["eq"] = canon
            q.matched.append(f"Colour {canon}")
            claim(m.start(), m.end())

    for d in attr_defs:
        if d["type"] != "select":
            continue
        for opt in d.get("options", []):
            pat = r"\b" + re.escape(opt.lower()) + r"\b"
            for m in re.finditer(pat, text):
                if is_free(m.start(), m.end()) and d["key"] not in q.attributes:
                    q.attributes.setdefault(d["key"], {})["eq"] = opt
                    q.matched.append(f"{d['label']} {opt}")
                    claim(m.start(), m.end())

    # ---- 8. Condition -------------------------------------------------
    for phrase, value in sorted(CONDITION_WORDS.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(r"\b" + re.escape(phrase) + r"\b", text):
            if is_free(m.start(), m.end()):
                claim(m.start(), m.end())
                if value and not q.condition:
                    q.condition = value
                    q.matched.append(value.title())
                break

    # An attribute's own name is noise once its value is captured.
    ATTR_WORDS = {a for d in attr_defs for a in d.get("aliases", [])}
    ATTR_WORDS |= {"colour", "color", "rs", "inr"}

    # ---- 9. Whatever is left becomes a title search -------------------
    leftover = "".join(
        " " if i in consumed else c for i, c in enumerate(text))
    q.keywords = [w for w in re.findall(r"[a-z0-9]+", leftover)
                  if w not in STOPWORDS and len(w) > 1
                  and w not in MULTIPLIERS
                  and w not in ATTR_WORDS
                  and not any(w in b.split() for b in UPPER_BOUND + LOWER_BOUND + RANGE_WORDS)]
    return q
