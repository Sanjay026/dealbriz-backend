"""Live marketplace answers for the chatbot.

Everything here is a real query against the current database. Nothing is
precomputed or hardcoded, so answers change as listings, partners and
coverage change — a bot that says "we have 12 laptops" needs to be counting,
not remembering.

Only PUBLIC, already-browsable information is exposed: active listings,
their prices, categories, published partners and coverage. No seller
identities, no buyer data, no draft or removed listings.
"""
import re

from sqlalchemy import func

from extensions import db
from models import EmiServiceArea, Faq, Financer, LegalDocument, Pincode, Product

from .intents import stem

MAX_ROWS = 6

# Category slug -> the words a user is likely to type for it.
# Singular nouns for replies — the slugs are plural, which made sentences
# like "3 phones listings" instead of "3 phones".
CATEGORY_LABELS = {
    "cars": "car", "phones": "phone", "bikes": "bike",
    "electronics": "electronics item", "furniture": "furniture item",
    "appliances": "appliance", "fashion": "fashion item", "property": "property listing",
}

CATEGORY_WORDS = {
    "cars": {"car", "cars", "sedan", "hatchback", "suv", "vehicle"},
    "phones": {"phone", "phones", "mobile", "smartphone", "iphone", "android"},
    "bikes": {"bike", "bikes", "motorcycle", "scooter", "scooty", "two-wheeler"},
    "electronics": {"electronics", "laptop", "laptops", "tv", "camera", "computer", "console"},
    "furniture": {"furniture", "sofa", "bed", "table", "chair", "wardrobe"},
    "appliances": {"appliance", "appliances", "fridge", "refrigerator", "washing", "ac", "microwave"},
    "fashion": {"fashion", "clothes", "clothing", "shoes", "watch", "bag"},
    "property": {"property", "flat", "house", "apartment", "plot", "land", "shop"},
}


def _money(v):
    try:
        return f"\u20b9{int(float(v)):,}"
    except (TypeError, ValueError):
        return "\u2014"


def _active():
    return Product.query.filter_by(status="active")


# Word sets are matched on STEMS, not literals. Hand-listing every plural
# is exactly the kind of thing that goes wrong quietly: "phones" was in the
# set and "mobiles" wasn't, so "how many mobiles near me" matched nothing.
_CATEGORY_STEMS = {
    slug: {stem(w) for w in terms} | set(terms)
    for slug, terms in CATEGORY_WORDS.items()
}


def detect_category(message):
    text = re.sub(r"[^a-z0-9\s]", " ", (message or "").lower())
    words = set(text.split())
    words |= {stem(w) for w in words}
    for slug, terms in _CATEGORY_STEMS.items():
        if words & terms:
            return slug
    return None


# ---------------------------------------------------------------------
# Location awareness
# ---------------------------------------------------------------------
NEAR_ME_WORDS = ("near me", "nearby", "in my area", "around me", "close to me",
                 "my city", "my area", "near by", "locally", "in my city")


def wants_local(message):
    text = (message or "").lower()
    return any(w in text for w in NEAR_ME_WORDS)


def resolve_scope(message, viewer_pincode=None):
    """Where the user means. Either a place named in the question, or — for
    "near me" — the area they've selected in the app.

    Returns (label, district, state) or (None, None, None)."""
    # An explicit 6-digit pincode in the question wins over anything else.
    pin = re.search(r"\b(\d{6})\b", message or "")
    if pin:
        row = Pincode.query.filter_by(pincode=pin.group(1)).first()
        if row:
            return (f"{row.district}, {row.state}", row.district, row.state)

    # A place named by word: "phones in Mandi", "cars in Kerala".
    m = re.search(r"\b(?:in|near|around|at)\s+([a-z][a-z\s]{2,30})$", (message or "").lower().strip())
    if m:
        place = m.group(1).strip().title()
        if place.lower() not in ("me", "my area", "my city", "here"):
            row = (Pincode.query.filter(Pincode.district == place).first()
                   or Pincode.query.filter(Pincode.state == place).first())
            if row:
                if row.district.lower() == place.lower():
                    return (f"{row.district}, {row.state}", row.district, None)
                return (row.state, None, row.state)

    # "near me" -> whatever area the user has selected in the app.
    if wants_local(message) and viewer_pincode:
        row = Pincode.query.filter_by(pincode=viewer_pincode).first()
        if row:
            return (row.district, row.district, None)
    return (None, None, None)


def _pincodes_for(district=None, state=None):
    """Product.city stores a pincode, so a district or state filter has to
    become the set of pincodes inside it."""
    q = db.session.query(Pincode.pincode)
    if district:
        q = q.filter(Pincode.district == district)
    elif state:
        q = q.filter(Pincode.state == state)
    else:
        return None
    return [r[0] for r in q.distinct().all()]


# ---------------------------------------------------------------------
# Handlers. Each returns (text, action) or None when it has nothing to say —
# returning None lets the resolver fall through rather than answering badly.
# ---------------------------------------------------------------------

def inventory_search(message, viewer_pincode=None):
    """"do you have laptops", "how many mobiles near me", "cars in Mandi
    under 5 lakh"."""
    category = detect_category(message)
    if not category:
        return None

    q = _active().filter(Product.category == category)

    # Location, if the question implies one. "near me" uses the area the
    # user has selected in the app; a named place or pincode overrides it.
    where_label, district, state = resolve_scope(message, viewer_pincode)
    if district or state:
        pins = _pincodes_for(district, state)
        if pins:
            q = q.filter(Product.city.in_(pins))
        else:
            where_label = None   # unknown place: answer nationally, and say so

    # Honour a price ceiling if one was mentioned: "under 50000", "below 2 lakh".
    ceiling = None
    m = re.search(r"(?:under|below|less than|upto|up to|within)\s*\u20b9?\s*([\d,]+)\s*(lakh|lac|k)?",
                  (message or "").lower())
    if m:
        try:
            ceiling = float(m.group(1).replace(",", ""))
            unit = m.group(2)
            if unit in ("lakh", "lac"):
                ceiling *= 100000
            elif unit == "k":
                ceiling *= 1000
        except ValueError:
            ceiling = None
    if ceiling:
        q = q.filter(Product.price <= ceiling)

    total = q.count()
    # Category slugs are already plural ("phones"), so "3 phones listings"
    # reads wrong. Use the singular as the noun and pluralise once.
    label = CATEGORY_LABELS.get(category, category.replace("_", " "))
    where = f" in **{where_label}**" if where_label else ""

    if not total:
        # Say what was actually searched, so "no results" is interpretable
        # rather than looking like the bot simply failed.
        return (f"There are no active **{label}** listings{where}"
                + (f" under {_money(ceiling)}" if ceiling else "")
                + " right now."
                + (" Try a wider area or a different budget." if (where or ceiling)
                   else " New items are added daily."),
                {"label": f"Browse {label}", "page": "buy"})

    rows = q.order_by(Product.price.asc()).limit(MAX_ROWS).all()
    lo = min(float(r.price) for r in rows)
    lines = [f"\u2022 {r.title} \u2014 {_money(r.price)}" for r in rows]
    head = (f"There {'is' if total == 1 else 'are'} **{total} {label}"
            f"{'s' if total != 1 else ''}**{where}"
            + (f" under {_money(ceiling)}" if ceiling else "")
            + f", starting at {_money(lo)}:")
    more = f"\n\n...and {total - len(rows)} more." if total > len(rows) else ""
    return (head + "\n\n" + "\n".join(lines) + more,
            {"label": f"Browse {label}", "page": "buy"})


def cheapest_or_priciest(message, viewer_pincode=None):
    text = (message or "").lower()
    wants_cheap = any(w in text for w in ("cheapest", "lowest", "least expensive", "most affordable"))
    wants_dear = any(w in text for w in ("most expensive", "priciest", "highest", "costliest"))
    if not (wants_cheap or wants_dear):
        return None

    q = _active()
    category = detect_category(message)
    if category:
        q = q.filter(Product.category == category)
    where_label, district, state = resolve_scope(message, viewer_pincode)
    if district or state:
        pins = _pincodes_for(district, state)
        if pins:
            q = q.filter(Product.city.in_(pins))
        else:
            where_label = None
    row = q.order_by(Product.price.asc() if wants_cheap else Product.price.desc()).first()
    if not row:
        return ("I couldn't find any active listings to compare.", {"label": "Browse", "page": "buy"})
    what = f"{category} " if category else ""
    where = f" in {where_label}" if where_label else ""
    return (f"The {'cheapest' if wants_cheap else 'most expensive'} active {what}listing{where} is "
            f"**{row.title}** at {_money(row.price)}.", {"label": "Browse listings", "page": "buy"})


def catalogue_overview(message, viewer_pincode=None):
    text = (message or "").lower()
    if not any(w in text for w in ("what do you sell", "what can i buy", "categories",
                                   "what is available", "what products", "how many listings",
                                   "what do you have")):
        return None
    rows = (db.session.query(Product.category, func.count(Product.id))
            .filter(Product.status == "active").group_by(Product.category)
            .order_by(func.count(Product.id).desc()).all())
    total = sum(c for _, c in rows)
    if not total:
        return ("There are no active listings yet.", {"label": "Browse", "page": "buy"})
    lines = [f"\u2022 {cat.title()} \u2014 {count}" for cat, count in rows]
    return (f"There are **{total} active listings** across {len(rows)} categories:\n\n"
            + "\n".join(lines), {"label": "Browse all", "page": "buy"})


def emi_partners(message, viewer_pincode=None):
    text = (message or "").lower()
    if not any(w in text for w in ("partner", "lender", "financer", "financier",
                                   "who provides emi", "which bank")):
        return None
    rows = Financer.query.filter_by(is_active=True).all() if hasattr(Financer, "is_active") \
        else Financer.query.all()
    if not rows:
        return ("We're still finalising our financing partners, so EMI isn't live yet.",
                {"label": "Contact Us", "page": "contact"})
    lines = []
    for f in rows[:MAX_ROWS]:
        bits = [f"\u2022 **{f.name}**"]
        if getattr(f, "min_interest_rate", None) is not None and getattr(f, "max_interest_rate", None) is not None:
            bits.append(f"{f.min_interest_rate}%\u2013{f.max_interest_rate}% p.a.")
        lines.append(" \u2014 ".join(bits))
    return (f"We work with **{len(rows)} financing partner"
            f"{'s' if len(rows) != 1 else ''}**:\n\n" + "\n".join(lines),
            {"label": "See partners", "page": "partners"})


def emi_coverage(message, viewer_pincode=None):
    """"is emi available in Mandi", "do you offer emi in 175001"."""
    text = (message or "").lower()
    if "emi" not in text and "finance" not in text:
        return None
    if not any(w in text for w in ("available in", "in my area", "offer emi in",
                                   "cover", "my city", "my location", "near me")):
        # Also catch a bare pincode alongside "emi".
        if not re.search(r"\b\d{6}\b", text):
            return None

    pin = re.search(r"\b(\d{6})\b", text)
    from locations.routes import resolve_emi_availability

    if pin:
        res = resolve_emi_availability(pincode=pin.group(1))
        row = Pincode.query.filter_by(pincode=pin.group(1)).first()
        where = f"{row.district}, {row.state}" if row else pin.group(1)
        if res.get("available"):
            return (f"Yes \u2014 EMI is available in **{where}** ({pin.group(1)}).", None)
        return (f"EMI isn't available in **{where}** ({pin.group(1)}) yet.",
                {"label": "Contact Us", "page": "contact"})

    if viewer_pincode and wants_local(message):
        res = resolve_emi_availability(pincode=viewer_pincode)
        row = Pincode.query.filter_by(pincode=viewer_pincode).first()
        where = f"{row.district}, {row.state}" if row else viewer_pincode
        if res.get("available"):
            return (f"Yes \u2014 EMI is available in **{where}**.", None)
        return (f"EMI isn't available in **{where}** yet.",
                {"label": "Contact Us", "page": "contact"})

    # No pincode given: describe where it IS offered rather than guessing.
    areas = EmiServiceArea.query.filter_by(is_enabled=True).all()
    if not areas:
        return ("EMI isn't live in any region yet \u2014 we're finalising our lending partners.",
                {"label": "Contact Us", "page": "contact"})
    names = ", ".join(a.scope_value for a in areas[:MAX_ROWS])
    return (f"EMI is currently offered in: **{names}**.\n\n"
            "Tell me your 6-digit pincode and I'll check it exactly.", None)


def policy_lookup(message, viewer_pincode=None):
    """Points at the real policy pages rather than paraphrasing them — a
    summarised legal document is a liability, not a convenience."""
    text = (message or "").lower()
    targets = [
        (("privacy", "my data", "personal information", "data protection"), "privacy", "Privacy Policy"),
        (("terms", "conditions", "agreement"), "terms", "Terms of Service"),
        (("cookie", "cookies"), "cookies", "Cookie Policy"),
        (("about dealbriz", "who are you", "about the company"), "about", "About DealBridge"),
    ]
    for words, key, label in targets:
        if any(w in text for w in words):
            doc = LegalDocument.query.get(key)
            if not doc:
                continue
            first = next((ln.strip() for ln in (doc.content or "").split("\n")
                          if ln.strip() and not ln.strip().startswith("**")), "")
            return (f"Our **{label}** covers that."
                    + (f"\n\n{first[:220]}" if first else ""),
                    {"label": f"Read {label}", "page": "legal", "doc": key})
    return None


# Order matters: the more specific a handler, the earlier it runs.
MARKETPLACE_HANDLERS = [
    ("emi_coverage", emi_coverage),
    ("emi_partners", emi_partners),
    ("cheapest", cheapest_or_priciest),
    ("catalogue", catalogue_overview),
    ("inventory", inventory_search),
    ("policy", policy_lookup),
]


def try_marketplace(message, viewer_pincode=None):
    for name, handler in MARKETPLACE_HANDLERS:
        try:
            result = handler(message, viewer_pincode)
        except Exception:
            continue          # a broken handler must not answer wrongly
        if result:
            return name, result
    return None, None
