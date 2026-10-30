"""Categories: the public list, and Admin -> Categories.

  GET    /api/categories                   public: shown categories + live listing counts
  GET    /api/admin/categories             staff (categories.manage): all, with counts
  POST   /api/admin/categories             add {slug, label, icon, emoji, is_vehicle, is_active}
  PUT    /api/admin/categories/<slug>      edit label, icon, emoji, is_active, is_vehicle
  POST   /api/admin/categories/reorder     {slugs: [...]} in the new order
  DELETE /api/admin/categories/<slug>      only when no listing has ever used it

The slug can't change: it's stored on every listing and in /category/ URLs.
"""
import re

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from extensions import db
from permissions import require_capability

from . import util

bp = Blueprint("categories", __name__)

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")


def _counts(statuses=("active",)):
    from models import Product
    rows = (db.session.query(Product.category, func.count(Product.id))
            .filter(Product.status.in_(statuses)).group_by(Product.category).all())
    return {c: n for c, n in rows}


@bp.route("/api/categories", methods=["GET"])
def public_categories():
    """The categories the site and the app show, in order, with how many
    live listings each has (what the home tiles display)."""
    counts = _counts(("active", "reserved"))
    out = [dict(c, listing_count=counts.get(c["slug"], 0)) for c in util.active_categories()]
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# ------------------------------------------------------------------ admin

def _clean(data, creating):
    """Validated fields from the request, or raises ValueError(message)."""
    from validation import Invalid, short_text
    out = {}
    if creating:
        slug = str(data.get("slug") or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise ValueError("The key must be 2-30 lowercase letters, numbers or _ and start with a letter "
                             "(e.g. scooters). It can't be changed later.")
        out["slug"] = slug
    try:
        if creating or "label" in data:
            out["label"] = short_text(data.get("label"), "name", 60, required=True)
        if "emoji" in data or creating:
            emoji = str(data.get("emoji") or "📦").strip()
            if len(emoji) > 16 or re.search(r"[<>\"'&\s]", emoji):
                raise ValueError("The emoji should be a single emoji.")
            out["emoji"] = emoji
    except Invalid as exc:
        raise ValueError(exc.message)
    if "icon" in data or creating:
        icon = str(data.get("icon") or "fa-box-open").strip()
        if icon not in util.ICONS:
            raise ValueError("Pick an icon from the list.")
        out["icon"] = icon
    for key in ("is_active", "is_vehicle"):
        if key in data:
            out[key] = data.get(key) is True or str(data.get(key)).lower() in ("1", "true", "yes", "on")
    return out


def _admin_view(cat, counts_all, counts_live):
    d = cat.to_dict()
    d["listing_count"] = counts_live.get(cat.slug, 0)
    d["listing_count_all"] = counts_all.get(cat.slug, 0)
    return d


@bp.route("/api/admin/categories", methods=["GET"])
@require_capability("categories.manage")
def admin_list():
    from models import Category, Product
    cats = Category.query.order_by(Category.sort_order.asc(), Category.label.asc()).all()
    all_counts = dict(db.session.query(Product.category, func.count(Product.id)).group_by(Product.category).all())
    live = _counts(("active", "reserved"))
    known = {c.slug for c in cats}
    # Listings whose category isn't in the table (e.g. typed by an old app
    # version): shown so staff can add the category or move the listings.
    orphans = [{"slug": s, "listing_count_all": n} for s, n in all_counts.items() if s not in known]
    return jsonify({"categories": [_admin_view(c, all_counts, live) for c in cats],
                    "icons": util.ICONS, "unknown_in_listings": orphans})


@bp.route("/api/admin/categories", methods=["POST"])
@require_capability("categories.manage")
def admin_create():
    from models import Category
    try:
        fields = _clean(request.get_json(silent=True) or {}, creating=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if Category.query.get(fields["slug"]):
        return jsonify({"error": "A category with that key already exists."}), 409
    last = db.session.query(func.max(Category.sort_order)).scalar() or 0
    cat = Category(sort_order=last + 10, **fields)
    db.session.add(cat)
    db.session.commit()
    util.clear_cache()
    return jsonify(cat.to_dict()), 201


@bp.route("/api/admin/categories/<slug>", methods=["PUT"])
@require_capability("categories.manage")
def admin_update(slug):
    from models import Category
    cat = Category.query.get(slug)
    if not cat:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    if "slug" in data and data["slug"] != slug:
        return jsonify({"error": "The key can't be changed: listings and links use it."}), 400
    try:
        fields = _clean(data, creating=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if fields.get("is_active") is False and cat.is_active:
        if Category.query.filter(Category.is_active.is_(True), Category.slug != slug).count() == 0:
            return jsonify({"error": "At least one category must stay visible."}), 409
    for k, v in fields.items():
        setattr(cat, k, v)
    db.session.commit()
    util.clear_cache()
    return jsonify(cat.to_dict())


@bp.route("/api/admin/categories/reorder", methods=["POST"])
@require_capability("categories.manage")
def admin_reorder():
    from models import Category
    slugs = (request.get_json(silent=True) or {}).get("slugs")
    if not isinstance(slugs, list) or not slugs or len(slugs) > 200:
        return jsonify({"error": "Send the categories in their new order."}), 400
    cats = {c.slug: c for c in Category.query.all()}
    if set(slugs) != set(cats) or len(slugs) != len(cats):
        return jsonify({"error": "The list doesn't match the categories. Refresh and try again."}), 409
    for i, s in enumerate(slugs):
        cats[s].sort_order = (i + 1) * 10
    db.session.commit()
    util.clear_cache()
    return jsonify({"ok": True})


@bp.route("/api/admin/categories/<slug>", methods=["DELETE"])
@require_capability("categories.manage")
def admin_delete(slug):
    from models import Category, Product
    cat = Category.query.get(slug)
    if not cat:
        return jsonify({"error": "Not found"}), 404
    used = Product.query.filter_by(category=slug).count()
    if used:
        return jsonify({"error": f"{used} listing(s) use this category, so it can't be deleted. "
                                 "Hide it instead."}), 409
    if Category.query.filter(Category.is_active.is_(True), Category.slug != slug).count() == 0:
        return jsonify({"error": "At least one category must stay visible."}), 409
    db.session.delete(cat)
    db.session.commit()
    util.clear_cache()
    return jsonify({"ok": True})
