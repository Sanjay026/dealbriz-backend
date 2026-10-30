import os
import uuid

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required

from extensions import db
from models import Product, Pincode
from models.product import normalize_show_phone, SHOW_PHONE_NO, SHOW_PHONE_YES
from utils import current_user

bp = Blueprint("sell", __name__, url_prefix="/api/sell")



def resolve_place_from_pincode(pincode):
    """Return "District, State" for a pincode, or "" if it isn't in the
    India Post table.

    Sellers routinely fill in the pincode and skip the free-text location,
    which left listings showing a bare six-digit number and nothing a buyer
    could recognise. The pincodes table already carries district and state,
    so the readable name is derived once here, at save time, and stored on
    the listing.

    Deliberately resolved on save rather than on read: the product list and
    the search results render many listings at once, and a lookup per
    listing would be one extra query per row on every browse page.

    A pincode can host several post offices (the table's primary key is
    pincode + office_name), but they share a district, so any row answers
    the question. is_major is preferred only for determinism.
    """
    if not pincode:
        return ""
    row = (Pincode.query
           .filter_by(pincode=str(pincode).strip())
           .order_by(Pincode.is_major.desc())
           .first())
    if not row:
        return ""
    return f"{row.district}, {row.state}"


@bp.route("", methods=["POST"])
@jwt_required()
def create_product():
    user = current_user()
    # DealLock: 3 force-cancelled deals in a row pause new listings for a
    # week (deals/rules.py CANCEL_LIMIT and BAN_DAYS).
    from deals.service import ban_message, is_banned
    if is_banned(user):
        return jsonify({"error": ban_message(user), "deal_ban_until": user.to_dict()["deal_ban_until"]}), 403
    data = request.get_json(silent=True) or {}
    required = ["title", "category", "condition", "price", "city"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": "Missing required fields"}), 400
    # Required on every client (the website's form already insisted). The
    # app used to post a stock sentence in its place.
    if not str(data.get("description") or "").strip():
        return jsonify({"error": "Please add a description."}), 400

    # Every field checked on the server (validation.py): a direct POST skips
    # the form. Price in range, short fields without < >, and photos only
    # from our own storage, since they end up inside <img src="...">.
    from validation import Invalid, clean_listing
    try:
        clean = clean_listing(data)
    except Invalid as exc:
        return jsonify({"error": exc.message}), 400
    data.update(clean)
    extra = clean.get("extra_images") or []

    p = Product(
        title=data["title"], category=data["category"], condition=data["condition"],
        price=data["price"], original_price=data.get("original_price"),
        negotiable=bool(data.get("negotiable", False)), emi_eligible=bool(data.get("emi_eligible", True)),
        description=data["description"], image_url=data.get("image_url"),
        extra_images=",".join(extra) if extra else None,
        posted_by=user.id, seller_name=f"{user.first_name} {user.last_name}".strip(),
        seller_avatar=user.avatar_url, seller_phone=user.phone,
        # The account-wide Profile setting decides, in both directions. The
        # app doesn't send a per-listing value at all, so falling back to the
        # request when the setting is ON posted every new app ad as hidden.
        # Only accounts that never used the setting (website-only users) get
        # the per-listing choice from the website's form.
        show_phone=(SHOW_PHONE_YES if user.show_phone is True
                    else SHOW_PHONE_NO if user.show_phone is False
                    else normalize_show_phone(data.get("show_phone", SHOW_PHONE_NO))),
        # Fall back to the pincode's district/state when the seller left the
        # location blank, so the listing never shows only a number.
        location=(data.get("location") or "").strip() or resolve_place_from_pincode(data["city"]),
        city=data["city"], landmark=data.get("landmark", ""),
        brand=data.get("brand", ""), model=data.get("model", ""), status="active"
    )
    # DealLock: for a car or bike, the seller's answer to "has this vehicle
    # ever had a loan" (decides whether the loan NOC is among its documents).
    # The documents themselves are uploaded next, to /api/listing-docs.
    from deals import rules as deal_rules
    had = data.get("vehicle_had_loan")
    if isinstance(had, bool) and deal_rules.is_vehicle_category(p.category):
        p.vehicle_had_loan = had
    db.session.add(p)
    db.session.flush()          # need p.id before attaching attributes

    save_product_attributes(p, data.get("attributes") or {})

    db.session.commit()
    # The parser learns brands and models from live listings, so a new one
    # must invalidate that cache or it stays unsearchable for 5 minutes.
    try:
        from smart_search import clear_vocabulary_cache
        clear_vocabulary_cache()
    except Exception:
        pass
    return jsonify(p.to_dict()), 201


def save_product_attributes(product, submitted):
    """Stores the category-specific fields (year, km, colour...).

    Writes them TWICE, on purpose, to two places with different jobs:

      products.attributes_json   the source of truth. Travels with the
                                 product row, so anything that can load a
                                 listing has its attributes by definition.
      product_attributes         a derived search index, wiped and rebuilt
                                 from the JSON on every save. smart_search
                                 needs its indexed attr_num column for range
                                 filters ("under 1 lakh km"); doing that
                                 against JSON would need a functional index
                                 per field and be markedly slower.

    Nothing writes the index by hand, so the two cannot drift: the index is
    always a projection of what is in the JSON.

    Only keys defined for that category are accepted — an arbitrary key from
    the request body would otherwise let anyone write junk that then shows on
    the listing and in filters.
    """
    from attributes import attributes_for, normalise_value, numeric_value
    from models import ProductAttribute

    allowed = {a["key"]: a for a in attributes_for(product.category)}

    clean = {}
    for key, raw in (submitted or {}).items():
        if key not in allowed:
            continue
        value = normalise_value(key, raw)
        if value is None:
            continue

        # Whole numbers only for numeric fields, and never negative. The form
        # enforces this too, but a direct POST does not go through the form:
        # "2.5 owners" or "-2018" would otherwise be stored, displayed, and
        # matched by range filters.
        if allowed[key].get("type") == "number":
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if n < 0:
                continue
            value = int(n)

        clean[key] = str(value)[:120]

    # Source of truth first. None rather than {} when a category has no
    # attributes, so "never had any" stays distinct from "had some, cleared".
    product.attributes_json = clean or None

    # Then rebuild the index from exactly what was just stored.
    ProductAttribute.query.filter_by(product_id=product.id).delete()
    for key, value in clean.items():
        db.session.add(ProductAttribute(
            product_id=product.id, attr_key=key,
            attr_value=value, attr_num=numeric_value(key, value),
        ))


@bp.route("/attributes/<category>", methods=["GET"])
def category_attributes(category):
    """Field definitions for a category, so the sell form renders the right
    inputs without duplicating the list in JavaScript."""
    from attributes import attributes_for
    return jsonify({"category": category, "attributes": attributes_for(category)})


@bp.route("/upload-image", methods=["POST"])
@jwt_required()
def upload_product_image():
    user = current_user()
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Validated by the file's own bytes (storage.check_image), not its name
    # or claimed type: an unrestricted upload endpoint is how a marketplace
    # ends up hosting someone else's malware or script.
    from storage import check_image, save_upload
    ext, content_type, problem = check_image(file)
    if problem:
        return jsonify({"error": problem}), 400

    filename = f"product_{user.id}_{uuid.uuid4().hex}.{ext}"
    try:
        url = save_upload(file, filename, content_type)
    except Exception:
        current_app.logger.error("Upload failed", exc_info=True)
        return jsonify({"error": "Could not save that image. Please try again."}), 500
    return jsonify({"url": url})
