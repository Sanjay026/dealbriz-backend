"""Natural-language search over listings.

The parser turns a sentence into filters; this turns those filters into
SQL. Constraints are applied as real WHERE clauses on indexed columns, so
"under 5 lac" means exactly that rather than approximately that.
"""
from sqlalchemy import and_, case, func, or_

from extensions import db
from models import Product, Pincode
from query_parser import parse_query

# Brand and model vocabulary is read from the listings themselves, so the
# parser recognises whatever is actually for sale. Cached because it is
# needed on every keystroke of the suggestion endpoint and changes only
# when someone posts a listing.
_vocab_cache = {"brands": None, "models": None, "stamp": None}
VOCAB_TTL_SECONDS = 300


def _vocabulary():
    import time
    now = time.time()
    if _vocab_cache["stamp"] and now - _vocab_cache["stamp"] < VOCAB_TTL_SECONDS:
        return _vocab_cache["brands"], _vocab_cache["models"]
    try:
        brands = [r[0] for r in db.session.query(Product.brand)
                  .filter(Product.brand.isnot(None), Product.brand != "")
                  .distinct().limit(500).all()]
        models = [r[0] for r in db.session.query(Product.model)
                  .filter(Product.model.isnot(None), Product.model != "")
                  .distinct().limit(1000).all()]
    except Exception:
        return [], []
    _vocab_cache.update({"brands": brands, "models": models, "stamp": now})
    return brands, models


def clear_vocabulary_cache():
    _vocab_cache["stamp"] = None


def parse_search(raw):
    brands, models = _vocabulary()
    return parse_query(raw, brands, models)


def _attribute_clause(key, ops):
    """One EXISTS per attribute.

    A join would multiply rows when several attributes match, and then need
    a DISTINCT that defeats the index. EXISTS keeps it one row per product.
    """
    from models import ProductAttribute

    conds = [ProductAttribute.product_id == Product.id,
             ProductAttribute.attr_key == key]
    for op, value in ops.items():
        if op == "eq":
            if isinstance(value, str):
                conds.append(ProductAttribute.attr_value == value)
            else:
                conds.append(ProductAttribute.attr_num == value)
        elif op == "max":
            conds.append(ProductAttribute.attr_num <= value)
        elif op == "min":
            conds.append(ProductAttribute.attr_num >= value)
    return db.session.query(ProductAttribute).filter(and_(*conds)).exists()


def _pincode_places():
    """Deduplicated pincode -> district/state lookup, as a subquery.

    A pincode hosts several post offices, so the pincodes table has several
    rows per code. Joining Product to it directly would multiply every
    listing by its number of post offices. Grouping first keeps the join
    one-to-one. The offices of one pincode always share a district, so
    MIN() picks a value rather than losing information.
    """
    return (db.session.query(
        Pincode.pincode.label("pincode"),
        func.min(Pincode.district).label("district"),
        func.min(Pincode.state).label("state"))
        .group_by(Pincode.pincode).subquery())


def proximity_context(pincode):
    """Resolve a buyer's pincode into the district and state it sits in.

    Returns None when the pincode is unknown (or the pincodes table has not
    been loaded), which callers treat as "no location known" and fall back
    to ordinary ordering.
    """
    if not pincode or len(str(pincode).strip()) != 6:
        return None
    row = (Pincode.query
           .filter_by(pincode=str(pincode).strip())
           .order_by(Pincode.is_major.desc())
           .first())
    if not row:
        return None
    return {"pincode": row.pincode, "district": row.district, "state": row.state}


def proximity_rank(places, ctx):
    """0 = same pincode, 1 = same district, 2 = same state, 3 = elsewhere.

    Ordering, never filtering. A listing one street away can sit in a
    different pincode, so excluding on an exact match hides the very
    listings a buyer would most want. Sorting instead keeps everything
    reachable while still putting the closest first.
    """
    return case(
        (Product.city == ctx["pincode"], 0),
        (places.c.district == ctx["district"], 1),
        (places.c.state == ctx["state"], 2),
        else_=3,
    )


def apply_parsed_filters(query, parsed):
    """Adds every parsed constraint to a Product query."""
    if parsed.category:
        query = query.filter(Product.category == parsed.category)
    if parsed.brand:
        query = query.filter(Product.brand == parsed.brand)
    if parsed.model:
        query = query.filter(Product.model == parsed.model)
    if parsed.condition:
        query = query.filter(Product.condition == parsed.condition)
    if parsed.price_min is not None:
        query = query.filter(Product.price >= parsed.price_min)
    if parsed.price_max is not None:
        query = query.filter(Product.price <= parsed.price_max)

    for key, ops in (parsed.attributes or {}).items():
        query = query.filter(_attribute_clause(key, ops))

    # Leftover words match the title or description. AND rather than OR:
    # a user typing two extra words means both, and OR would widen the
    # result set exactly when they were trying to narrow it.
    for word in parsed.keywords[:6]:
        like = f"%{word}%"
        query = query.filter(or_(Product.title.like(like),
                                 Product.description.like(like)))
    return query


def relevance_order(query, parsed, first_key=None):
    """Most specific matches first.

    A listing whose title starts with what was typed is almost always the
    one wanted, so it outranks one that merely contains the words.
    """
    # Distance outranks text relevance when the buyer has a location: a
    # nearby near-match beats a perfect match on the far side of the country.
    lead = [first_key] if first_key is not None else []
    if parsed.keywords:
        first = parsed.keywords[0]
        query = query.order_by(
            *lead,
            func.if_(Product.title.like(f"{first}%"), 0, 1),
            Product.featured_until.isnot(None).desc(),
            Product.created_at.desc(),
        )
    else:
        query = query.order_by(
            *lead,
            Product.featured_until.isnot(None).desc(),
            Product.created_at.desc(),
        )
    return query


def relax_filters(parsed):
    """Progressively looser versions of a query, for when the exact one
    returns nothing.

    Ordered by what a user is most likely to compromise on: an exact model
    before a budget, and a budget before the category. Returning "no
    results" for a query that has near-misses is a worse answer than
    showing them and saying what was loosened.
    """
    steps = []
    if parsed.model:
        steps.append(("model", f"Ignoring the exact model \u201c{parsed.model}\u201d"))
    if parsed.attributes:
        steps.append(("attributes", "Ignoring the detailed filters"))
    if parsed.price_max is not None:
        steps.append(("price_up", "Raising the budget by 25%"))
    if parsed.brand:
        steps.append(("brand", f"Ignoring the brand \u201c{parsed.brand}\u201d"))
    return steps


def apply_relaxation(parsed, step):
    """Returns a copy of `parsed` with one constraint loosened."""
    import copy
    p = copy.deepcopy(parsed)
    if step == "model":
        p.model = None
    elif step == "attributes":
        p.attributes = {}
    elif step == "price_up" and p.price_max:
        p.price_max = p.price_max * 1.25
    elif step == "brand":
        p.brand = None
    return p


def search_listings(raw, page=1, per_page=30, base_filters=None, near_pincode=None):
    """Runs a natural-language search.

    Returns the results plus what was understood, so the UI can show the
    interpretation as removable chips — a user who sees "Under ₹5,00,000"
    knows why a ₹6L car is missing, instead of assuming the search is broken.
    """
    parsed = parse_search(raw)

    # The buyer's area sorts the results; it never removes any. See
    # proximity_rank for why an exact-pincode filter is the wrong tool.
    ctx = proximity_context(near_pincode)
    places = _pincode_places() if ctx else None

    def build(p):
        q = Product.query.filter(Product.status == "active")
        if base_filters is not None:
            q = base_filters(q)
        q = apply_parsed_filters(q, p)
        if ctx:
            # Outer join: a listing whose pincode is missing from the India
            # Post table still appears, ranked last, rather than vanishing.
            q = q.outerjoin(places, places.c.pincode == Product.city)
            return relevance_order(q, p, first_key=proximity_rank(places, ctx))
        return relevance_order(q, p)

    query = build(parsed)
    total = query.count()

    relaxed_note = None
    used = parsed
    if total == 0:
        for step, note in relax_filters(parsed):
            candidate = apply_relaxation(parsed, step)
            trial = build(candidate)
            count = trial.count()
            if count:
                query, total, used, relaxed_note = trial, count, candidate, note
                break

    items = query.limit(per_page).offset((page - 1) * per_page).all()

    # How close the nearest result actually is, so the UI can say "nothing in
    # your pincode, showing nearby" instead of leaving the buyer to wonder
    # why a listing from another state is at the top.
    nearness = None
    if ctx and total:
        exact = (build(used).filter(Product.city == ctx["pincode"]).count())
        if exact:
            nearness = {"scope": "exact", "pincode": ctx["pincode"],
                        "district": ctx["district"], "count": exact}
        else:
            near = (build(used)
                    .filter(places.c.district == ctx["district"]).count())
            nearness = {"scope": "district" if near else "wider",
                        "pincode": ctx["pincode"],
                        "district": ctx["district"], "count": near}

    return {
        "items": items,
        "nearness": nearness,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "parsed": parsed.to_dict(),
        "relaxed": relaxed_note,
        "used": used.to_dict() if used is not parsed else None,
    }
