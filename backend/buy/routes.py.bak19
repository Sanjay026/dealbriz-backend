from datetime import datetime

from flask import Blueprint, request, jsonify
from sqlalchemy import case, func

from search_utils import expand_terms, normalise, is_short_term

from extensions import db
from models import Product

bp = Blueprint("buy", __name__, url_prefix="/api/buy")

MAX_PER_PAGE = 500  # matches the buyer-facing page-size selector's max
DEFAULT_PER_PAGE = 30


def _squash(col):
    """SQL-side equivalent of search_utils.normalise() for the characters
    that actually appear in listing titles. Lets "smart tv" in the database
    match a "smarttv" search and vice versa, without a separate index
    column. Costs a full scan on the text columns — acceptable at this
    catalogue size; if it ever isn't, the fix is a stored normalised
    column, not a different query shape."""
    lowered = func.lower(col)
    for ch in (" ", "-", "_", ".", "/", "'"):
        lowered = func.replace(lowered, ch, "")
    return lowered


def build_search_filter(search):
    """Match a listing if ANY expanded term appears in its title, brand,
    model or description. Terms come from search_utils.expand_terms(), so
    a search for "laptop" also matches "MacBook", and "smart tv" matches
    "SmartTV"."""
    terms = expand_terms(search)
    if not terms:
        return db.text("1=1")

    squashed_title = _squash(Product.title)
    squashed_brand = _squash(Product.brand)
    squashed_model = _squash(Product.model)
    squashed_desc = _squash(Product.description)

    clauses = []
    for term in terms:
        if is_short_term(term):
            # Word-boundary match for very short terms. We pad the text with
            # spaces and search for " term " against the ORIGINAL (unsquashed)
            # lowercase text, so "ac" matches "split AC" but not "MacBook".
            padded_title = func.concat(" ", func.lower(Product.title), " ")
            padded_brand = func.concat(" ", func.lower(Product.brand), " ")
            padded_model = func.concat(" ", func.lower(Product.model), " ")
            padded_desc = func.concat(" ", func.lower(Product.description), " ")
            word = f"% {term} %"
            clauses.append(db.or_(
                padded_title.like(word),
                padded_brand.like(word),
                padded_model.like(word),
                padded_desc.like(word),
            ))
        else:
            pattern = f"%{term}%"
            clauses.append(db.or_(
                squashed_title.like(pattern),
                squashed_brand.like(pattern),
                squashed_model.like(pattern),
                squashed_desc.like(pattern),
            ))
    return db.or_(*clauses)


def build_product_query(args):
    """Shared filter logic for the listing endpoint AND the search endpoint,
    so 'use the same api for categories' and 'create search api' both stay
    backed by one query builder instead of duplicating filter logic."""
    # Reserved listings (a DealLock deal is in progress) are left out unless
    # the client asks for them with include_reserved=1. A client that does
    # can show the "Reserved" badge; older builds that can't simply don't
    # list them, rather than showing them as if they were still for sale.
    if str(args.get("include_reserved", "")).lower() in ("1", "true", "yes"):
        q = Product.query.filter(Product.status.in_(("active", "reserved")))
    else:
        q = Product.query.filter_by(status="active")

    category = args.get("category")
    condition = args.get("condition")
    search = args.get("search") or args.get("q")
    min_price = args.get("min_price", type=float)
    max_price = args.get("max_price", type=float)
    pincode = args.get("pincode")
    emi_only = args.get("emi_only")
    sort = (args.get("sort") or "recommended").strip().lower()

    if category and category != "all":
        q = q.filter_by(category=category)
    if condition and condition != "all":
        q = q.filter_by(condition=condition)
    if search:
        q = q.filter(build_search_filter(search))
    if min_price is not None:
        q = q.filter(Product.price >= min_price)
    if max_price is not None:
        q = q.filter(Product.price <= max_price)
    if pincode:
        q = q.filter(Product.city.like(f"{pincode}%"))
    if emi_only == "true":
        q = q.filter_by(emi_eligible=True)

    # Two kinds of ordering.
    #
    # "recommended" (the default, and what any client that sends no sort
    # gets): promoted listings first, then title matches when searching,
    # then listings near the buyer, then newest. This is the old default
    # and the Android app's feed still relies on it.
    #
    # "newest", "price_asc", "price_desc": the buyer picked an order, so it
    # IS the order. These used to be applied only after promotion and
    # "near", so with an area selected the Newest/Price menu looked broken:
    # a 45-day-old nearby listing sat above yesterday's, and a promoted
    # listing sat above everything whatever the price. Nearness and age now
    # only break ties.
    #
    # Product.id last everywhere, so pagination is stable when two rows tie.
    near = (args.get("near") or "").strip()
    near_rank = case(
        (Product.city.like(f"{near}%"), 0),
        (Product.city.like(f"{near[:2]}%"), 1),
        else_=2
    ) if near else None

    if sort in ("price_asc", "price_desc"):
        order = [Product.price.asc() if sort == "price_asc" else Product.price.desc()]
        if near_rank is not None:
            order.append(near_rank)
        order += [Product.created_at.desc(), Product.id]
        return q.order_by(*order)

    if sort == "newest":
        return q.order_by(Product.created_at.desc(), Product.id)

    # --- recommended ---
    # Promoted listings sort above everything else, but ONLY within the
    # results the buyer's filters already selected — paying to be seen must
    # not put a fridge in front of someone searching for a phone.
    order = [case((Product.featured_until > datetime.utcnow(), 0), else_=1)]

    # Relevance next when searching: a listing whose TITLE matches should
    # outrank one that merely mentions the term in its description.
    if search:
        primary = normalise(search)
        if len(primary) >= 2:
            order.append(case(
                (_squash(Product.title).like(f"{primary}%"), 0),   # title starts with it
                (_squash(Product.title).like(f"%{primary}%"), 1),  # title contains it
                else_=2                                            # matched elsewhere
            ))

    # "near" ranks results without excluding anything: listings in the
    # buyer's district come first, then the wider postal circle, then
    # everywhere else. Done in SQL so it survives pagination.
    if near_rank is not None:
        order.append(near_rank)

    order += [Product.created_at.desc(), Product.id]
    return q.order_by(*order)


def paginated_response(q, args):
    page = max(args.get("page", 1, type=int) or 1, 1)
    per_page = args.get("per_page", DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE
    per_page = min(max(per_page, 1), MAX_PER_PAGE)

    total = q.count()
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page if total else 0

    return jsonify({
        "items": [p.to_dict() for p in items],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages
    })


@bp.route("", methods=["GET"])
def list_products():
    """Buy listing — filters + category + pagination all live on this one
    endpoint (30 items per page by default), per the ToDo note to reuse it
    for category browsing too."""
    q = build_product_query(request.args)
    return paginated_response(q, request.args)


@bp.route("/search", methods=["GET"])
def search_products():
    """Dedicated search endpoint for the search bar. Reuses the same
    filter/pagination logic as the main listing endpoint — pass the
    query text as ?q= (or ?search=)."""
    q = build_product_query(request.args)
    return paginated_response(q, request.args)


@bp.route("/<pid>", methods=["GET"])
def get_product(pid):
    p = Product.query.get(pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    d = p.to_dict()
    # DealLock: "pending" while a deal waits for the seller or documents
    # (the listing isn't marked reserved yet, but no one else can start a
    # deal), "reserved" once it locks, else None.
    from deals.service import deal_stage_for_products
    d["deal_stage"] = deal_stage_for_products([p]).get(p.id)
    return jsonify(d)


@bp.route("/<pid>/view", methods=["POST"])
def increment_view(pid):
    p = Product.query.get(pid)
    if p:
        p.views += 1
        db.session.commit()
    return jsonify({"ok": True})


# ===================== NATURAL-LANGUAGE SEARCH =====================

@bp.route("/smart-search", methods=["GET"])
def smart_search():
    """Search using a plain-English query.

        /api/buy/smart-search?q=hyundai car 2018 under 5 lac below 1 lac km

    Returns the listings plus what the query was understood to mean, so the
    UI can show the interpretation as removable chips. A user who sees
    "Under ₹5,00,000" understands why a ₹6L car is missing, instead of
    concluding the search is broken.
    """
    from smart_search import search_listings

    raw = (request.args.get("q") or request.args.get("search") or "").strip()
    if not raw:
        return jsonify({"items": [], "total": 0, "page": 1, "per_page": 30,
                        "total_pages": 0, "parsed": None})

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(max(1, int(request.args.get("per_page", 30))), 100)
    except (TypeError, ValueError):
        page, per_page = 1, 30

    # The buyer's area orders the results; it does not filter them.
    # This used to be `filter(Product.city == pincode)`, an exact-pincode
    # match, which meant a search for "phones" from Mohali found nothing
    # while the Phones category tab showed the listing — the category tab
    # only ever sorted by nearness. Two paths, two behaviours, and the
    # stricter one looked like a broken search. Now both rank by distance
    # and neither hides a listing for being a postcode away.
    pincode = (request.args.get("pincode") or "").strip()

    result = search_listings(raw, page=page, per_page=per_page,
                             near_pincode=pincode or None)
    items = [p.to_dict() for p in result.pop("items")]
    return jsonify({"items": items, **result})


@bp.route("/suggest", methods=["GET"])
def suggest():
    """Autocomplete for the search bar.

    Three kinds of suggestion, in descending usefulness: a completion of
    what they're typing, a matching listing title, and a refinement that
    adds a filter ("...under ₹50,000"). Capped tightly — a long dropdown
    is slower to read than typing the rest of the word.
    """
    from smart_search import parse_search

    raw = (request.args.get("q") or "").strip()
    if len(raw) < 2:
        return jsonify({"suggestions": []})

    parsed = parse_search(raw)
    like = f"%{raw}%"
    out, seen = [], set()

    def add(text, kind, note=None):
        key = text.lower()
        if key in seen or len(out) >= 8:
            return
        seen.add(key)
        out.append({"text": text, "type": kind, "note": note})

    # Matching titles — the strongest signal that something exists.
    try:
        titles = (Product.query.filter(Product.status == "active",
                                       Product.title.like(like))
                  .order_by(Product.featured_until.isnot(None).desc(),
                            Product.created_at.desc())
                  .limit(5).all())
        for p in titles:
            add(p.title, "listing", f"\u20b9{int(float(p.price)):,}")
    except Exception:
        pass

    # Brand / model completions.
    try:
        for col, kind in ((Product.brand, "brand"), (Product.model, "model")):
            rows = (db.session.query(col).filter(col.isnot(None), col.like(like))
                    .distinct().limit(4).all())
            for (value,) in rows:
                add(value, kind)
    except Exception:
        pass

    # Refinements: only offered once we understand enough for them to make
    # sense, otherwise they read as noise.
    if parsed.category or parsed.brand or parsed.model:
        if parsed.price_max is None:
            for amount, label in ((30000, "\u20b930,000"), (100000, "\u20b91 lakh"),
                                  (500000, "\u20b95 lakh")):
                add(f"{raw} under {label}", "refine", "add a budget")
        if "year" not in (parsed.attributes or {}) and parsed.category in ("cars", "bikes"):
            add(f"{raw} 2020 or newer", "refine", "add a year")

    return jsonify({"suggestions": out, "parsed": parsed.to_dict()})
