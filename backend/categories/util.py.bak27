"""Categories, read from the categories table (Admin -> Categories).

Read on almost every request (search, the sell form check, DealLock's
vehicle rule), so the table is cached in-process for CACHE_SECONDS. A save
in the admin panel clears this worker's copy at once; other gunicorn
workers pick it up within CACHE_SECONDS.

If the table is missing or empty (the migration hasn't run yet), the
built-in list below is used, so nothing breaks before the migration."""
import logging
import time

log = logging.getLogger("dealbridge.categories")

CACHE_SECONDS = 30

# slug, label, Font Awesome icon, emoji, vehicle. Also the migration's seed.
DEFAULTS = [
    ("cars", "Cars", "fa-car-side", "🚗", True),
    ("phones", "Phones", "fa-mobile-screen-button", "📱", False),
    ("bikes", "Bikes", "fa-motorcycle", "🏍️", True),
    ("electronics", "Electronics", "fa-laptop", "💻", False),
    ("furniture", "Furniture", "fa-couch", "🛋️", False),
    ("appliances", "Appliances", "fa-blender", "🧊", False),
    ("fashion", "Fashion", "fa-shirt", "👗", False),
    ("property", "Property", "fa-building", "🏢", False),
    ("others", "Others", "fa-box-open", "📦", False),
]

# Icons an admin can pick (Font Awesome 6 solid, all in the free set the
# site already loads). A fixed list: the value ends up in class="...".
ICONS = [
    "fa-car-side", "fa-car", "fa-truck", "fa-truck-pickup", "fa-van-shuttle", "fa-bus", "fa-tractor",
    "fa-motorcycle", "fa-bicycle", "fa-mobile-screen-button", "fa-tablet-screen-button", "fa-laptop",
    "fa-desktop", "fa-tv", "fa-camera", "fa-headphones", "fa-gamepad", "fa-couch", "fa-bed", "fa-chair",
    "fa-blender", "fa-plug", "fa-snowflake", "fa-fan", "fa-shirt", "fa-shoe-prints", "fa-gem", "fa-clock",
    "fa-building", "fa-house", "fa-store", "fa-warehouse", "fa-book", "fa-guitar", "fa-dumbbell",
    "fa-baby-carriage", "fa-paw", "fa-seedling", "fa-screwdriver-wrench", "fa-industry", "fa-box-open", "fa-box",
]

_cache = {"rows": None, "at": 0.0}


def clear_cache():
    _cache["rows"] = None


def _defaults():
    # Before the migration, vehicles follow DEALLOCK_VEHICLE_CATEGORIES as before.
    from deals.rules import VEHICLE_CATEGORIES
    return [{"slug": s, "label": l, "icon": i, "emoji": e, "sort_order": n * 10,
             "is_active": True, "is_vehicle": s in VEHICLE_CATEGORIES}
            for n, (s, l, i, e, _v) in enumerate(DEFAULTS)]


def all_categories():
    """Every category (shown and hidden) as dicts, in display order."""
    now = time.monotonic()
    if _cache["rows"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]
    rows = None
    try:
        from models import Category
        found = Category.query.order_by(Category.sort_order.asc(), Category.label.asc()).all()
        rows = [c.to_dict() for c in found] or None
    except Exception as exc:
        # Before the migration: the table doesn't exist yet.
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass
        log.warning("categories table not readable, using the built-in list: %s", exc.__class__.__name__)
    if rows is None:
        rows = _defaults()
    _cache.update(rows=rows, at=now)
    return rows


def active_categories():
    return [c for c in all_categories() if c["is_active"]]


def get(slug):
    slug = (slug or "").strip().lower()
    return next((c for c in all_categories() if c["slug"] == slug), None)


def is_vehicle(slug):
    c = get(slug)
    return bool(c and c["is_vehicle"])


def vehicle_slugs():
    return [c["slug"] for c in all_categories() if c["is_vehicle"]]


def label_for(slug):
    c = get(slug)
    return c["label"] if c else (slug or "").title()


def extra_search_words():
    """{slug: {words}} that name each category: its slug, the slug without a
    plural 's', and a one-word label. Added to search's and the chatbot's
    built-in synonyms, so a new category is recognised by its own name."""
    out = {}
    for c in all_categories():
        words = {c["slug"]}
        if c["slug"].endswith("s") and len(c["slug"]) > 3:
            words.add(c["slug"][:-1])
        label = c["label"].strip().lower()
        if label.isalpha():
            words.add(label)
            if label.endswith("s") and len(label) > 3:
                words.add(label[:-1])
        out[c["slug"]] = words
    return out


def merged_search_words(builtin):
    """builtin {slug: set} plus the words for categories added in the admin
    panel. The built-in categories keep exactly their hand-tuned words (so
    search behaves as before for them); only new ones get their own name."""
    merged = {k: set(v) for k, v in builtin.items()}
    try:
        for slug, words in extra_search_words().items():
            if slug not in merged and slug != "others":
                merged[slug] = words
    except Exception:
        log.warning("category words unavailable", exc_info=True)
    return merged
