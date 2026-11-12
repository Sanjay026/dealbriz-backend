"""The sell form's editable lists: brands per category, and the choices of
the dropdown fields (Colour, Fuel type, Storage...).

Edited in Admin -> Categories -> "Sell form lists" (categories/routes.py)
and served with the field definitions at GET /api/sell/attributes/<category>,
which the website and the app both read.

Stored in sell_option_lists, one row per list (models/sell_option.py). A list
with no row uses its default from attributes.py, so nothing changes until
an admin edits a list, and "Reset to default" just deletes the row.
"""
import json

from flask import current_app

from extensions import db

VALUE_MAX = 60      # characters in one entry
LIST_MAX = 300      # entries in one list


class ListError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def brand_key(slug):
    return f"brands:{slug}"


def attr_key(key):
    return f"attr:{key}"


def option_fields():
    """Every dropdown field of the sell form, once each, in the order they
    first appear: {key: {"label", "options"}}. Colour is shared by most
    categories, so it is one list for all of them."""
    from attributes import CATEGORY_ATTRIBUTES
    out = {}
    for attrs in CATEGORY_ATTRIBUTES.values():
        for a in attrs:
            if a.get("type") == "select" and a["key"] not in out:
                out[a["key"]] = {"label": a["label"], "options": list(a.get("options") or [])}
    return out


def _load(keys=None):
    """{list_key: [values]} for the lists that have been edited. Empty when
    the table doesn't exist yet (migration not run), so the defaults apply
    and the sell form keeps working."""
    from models import SellOptionList
    try:
        q = SellOptionList.query
        if keys is not None:
            q = q.filter(SellOptionList.list_key.in_(list(keys)))
        rows = q.all()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("sell_option_lists could not be read; using the default lists",
                                   exc_info=True)
        return {}
    out = {}
    for row in rows:
        try:
            values = json.loads(row.values_json or "[]")
        except ValueError:
            continue
        if isinstance(values, list):
            out[row.list_key] = [v for v in values if isinstance(v, str) and v.strip()]
    return out


def form_lists(category):
    """(brands, attribute definitions) for one category's sell form, with the
    admin's lists in place of the defaults. The definitions are copies: the
    module-level ones in attributes.py are never changed."""
    from attributes import DEFAULT_BRANDS, attributes_for
    attrs = attributes_for(category)
    keys = [brand_key(category)] + [attr_key(a["key"]) for a in attrs if a.get("type") == "select"]
    stored = _load(keys)
    brands = stored.get(brand_key(category), list(DEFAULT_BRANDS.get(category, [])))
    out = []
    for a in attrs:
        a = dict(a)
        if a.get("type") == "select" and attr_key(a["key"]) in stored:
            a["options"] = stored[attr_key(a["key"])]
        out.append(a)
    return brands, out


def clean_values(raw):
    """The entries of a list as sent by the admin panel (a JSON array, or
    one entry per line): trimmed, inner spaces collapsed, no duplicates
    (ignoring case), no "<" or ">". "Other"/"Others" is left out because the
    sell form adds its own choice for typing a value. Raises ListError."""
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        raise ListError("Send the list as one entry per line.")
    out, seen = [], set()
    for value in raw:
        text = " ".join(str(value if value is not None else "").split())
        if not text:
            continue
        if len(text) > VALUE_MAX:
            raise ListError(f"“{text[:30]}…” is too long (max {VALUE_MAX} characters).")
        if "<" in text or ">" in text:
            raise ListError(f"“{text}” can't contain < or >.")
        low = text.lower()
        if low in ("other", "others") or low in seen:
            continue
        seen.add(low)
        out.append(text)
    if len(out) > LIST_MAX:
        raise ListError(f"A list can have at most {LIST_MAX} entries.")
    return out


def known_key(list_key):
    """True for "brands:<a category that exists>" and "attr:<a dropdown field>"."""
    from categories import util as cat_util
    kind, _, name = (list_key or "").partition(":")
    if kind == "brands":
        return cat_util.get(name) is not None
    if kind == "attr":
        return name in option_fields()
    return False


def admin_lists():
    """Every editable list for the admin panel: its current entries, its
    defaults, and whether it has been edited."""
    from attributes import DEFAULT_BRANDS
    from categories import util as cat_util
    stored = _load()
    lists = []
    for c in cat_util.all_categories():
        key = brand_key(c["slug"])
        default = list(DEFAULT_BRANDS.get(c["slug"], []))
        lists.append({"key": key, "group": "brands", "label": f"Brands — {c['label']}",
                      "values": stored.get(key, default), "default": default,
                      "edited": key in stored})
    for name, field in option_fields().items():
        key = attr_key(name)
        lists.append({"key": key, "group": "fields", "label": field["label"],
                      "values": stored.get(key, field["options"]), "default": field["options"],
                      "edited": key in stored})
    return lists


def save_list(list_key, values, user_id):
    from models import SellOptionList
    row = SellOptionList.query.get(list_key)
    payload = json.dumps(values, ensure_ascii=False)
    if row is None:
        row = SellOptionList(list_key=list_key, values_json=payload, updated_by=user_id)
        db.session.add(row)
    else:
        row.values_json = payload
        row.updated_by = user_id
    db.session.commit()


def reset_list(list_key):
    from models import SellOptionList
    row = SellOptionList.query.get(list_key)
    if row is not None:
        db.session.delete(row)
        db.session.commit()
