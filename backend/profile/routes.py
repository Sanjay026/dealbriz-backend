import os
import re
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required

from extensions import db
from models import User, PushDevice, Product
from models.product import SHOW_PHONE_YES, SHOW_PHONE_NO
from utils import current_user, get_user_role_names, has_staff_access

# No url_prefix here: this blueprint owns a couple of related-but-distinct
# path groups (/api/profile/* and the public /api/users/<uid> lookup),
# so each route below spells out its own full path instead.
bp = Blueprint("profile", __name__)


@bp.route("/api/profile", methods=["GET"])
@jwt_required()
def get_profile():
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    data = user.to_dict()
    data["roles"] = get_user_role_names(user.id)
    data["is_staff"] = has_staff_access(user)
    return jsonify(data)


@bp.route("/api/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    user = current_user()
    data = request.get_json() or {}
    old_phone = user.phone
    for field in ["first_name", "last_name", "phone", "city"]:
        if field in data:
            setattr(user, field, data[field])

    # Listings keep their own copy of the seller's phone and show/hide flag
    # (that's what /api/buy returns), so account changes are copied onto all
    # of this user's listings - otherwise hiding the number in Profile would
    # leave it visible on every ad posted before.
    listing_updates = {}
    if "show_phone" in data:
        on = data["show_phone"] is True or str(data["show_phone"]).strip().lower() in ("yes", "1", "true")
        user.show_phone = on
        listing_updates[Product.show_phone] = SHOW_PHONE_YES if on else SHOW_PHONE_NO
    if "phone" in data and user.phone != old_phone:
        listing_updates[Product.seller_phone] = user.phone
    if listing_updates:
        Product.query.filter_by(posted_by=user.id).update(
            listing_updates, synchronize_session=False)

    db.session.commit()
    return jsonify(user.to_dict())


@bp.route("/api/profile/avatar", methods=["POST"])
@jwt_required()
def upload_avatar():
    """Stores a new profile picture.

    Two things were wrong here before and are fixed together:

    1. It wrote straight to the container's local disk with file.save().
       Listing images go through storage.save_upload(), which honours
       STORAGE_BACKEND and puts them in S3 in production. Avatars did not,
       so every `docker compose up -d --build` silently wiped them.

    2. It took whatever extension the filename carried, with no whitelist
       and no MIME check — unlike the listing endpoint right next to it. An
       unrestricted upload endpoint is how a site ends up serving someone
       else's HTML or SVG (which can carry script) from its own domain.
    """
    user = current_user()
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]

    ALLOWED = {"jpg", "jpeg", "png", "webp"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in ALLOWED:
        return jsonify({"error": "Only JPG, PNG or WEBP images are allowed"}), 400
    if file.mimetype and not file.mimetype.startswith("image/"):
        return jsonify({"error": "That file is not an image"}), 400

    old_url = user.avatar_url
    filename = f"avatar_{user.id}_{uuid.uuid4().hex}.{ext}"
    try:
        from storage import save_upload
        user.avatar_url = save_upload(file, filename)
    except Exception:
        current_app.logger.error("Avatar upload failed", exc_info=True)
        return jsonify({"error": "Could not save that image. Please try again."}), 500

    db.session.commit()

    # Replacing a picture leaves the previous file orphaned. Removed after the
    # commit, and failures ignored: a leftover file costs a few KB, while
    # failing the request after the profile already changed would be worse.
    if old_url:
        try:
            from storage import delete_upload
            delete_upload(old_url)
        except Exception:
            current_app.logger.warning("Could not remove old avatar %s", old_url)

    return jsonify({"avatar_url": user.avatar_url})


@bp.route("/api/profile/avatar", methods=["DELETE"])
@jwt_required()
def remove_avatar():
    """Clears the profile picture and deletes the stored file.

    The UI then falls back to drawn initials, so there is always something
    to show — no broken image, and no need for a default-avatar asset.
    """
    user = current_user()
    old_url = user.avatar_url
    if not old_url:
        return jsonify({"avatar_url": None}), 200

    user.avatar_url = None
    db.session.commit()

    try:
        from storage import delete_upload
        delete_upload(old_url)
    except Exception:
        current_app.logger.warning("Could not remove avatar %s", old_url)

    return jsonify({"avatar_url": None}), 200


# OneSignal subscription ids are UUIDs; anything else is rejected outright.
_PLAYER_ID_RE = re.compile(r"^[0-9A-Za-z-]{20,64}$")
_MAX_DEVICES_PER_USER = 10


@bp.route("/api/profile/push-device", methods=["POST"])
@jwt_required()
def register_push_device():
    """Links this phone's OneSignal subscription id to the signed-in user.

    Body: {"player_id": "<subscription id>", "platform": "android"}

    Idempotent - the app calls it on every sign-in and whenever OneSignal
    issues a new id. If the id was linked to another account (shared phone,
    or someone else signed in before), it moves to this user, so a phone
    only ever gets the current account's chats.
    """
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    platform = str(data.get("platform") or "android").strip().lower()[:20] or "android"
    if not _PLAYER_ID_RE.match(player_id):
        return jsonify({"error": "Invalid player_id"}), 400

    row = PushDevice.query.filter_by(player_id=player_id).first()
    if row:
        row.user_id = user.id
        row.platform = platform
        row.updated_at = datetime.utcnow()
    else:
        db.session.add(PushDevice(user_id=user.id, player_id=player_id, platform=platform))
    try:
        db.session.commit()
    except Exception:
        # Two requests for the same new id raced; the other one inserted it.
        db.session.rollback()
        row = PushDevice.query.filter_by(player_id=player_id).first()
        if not row:
            current_app.logger.error("push-device register failed", exc_info=True)
            return jsonify({"error": "Could not register device"}), 500
        row.user_id = user.id
        row.platform = platform
        db.session.commit()

    # Bound the fan-out per message: keep only the most recently seen devices.
    stale = (PushDevice.query.filter_by(user_id=user.id)
             .order_by(PushDevice.updated_at.desc())
             .offset(_MAX_DEVICES_PER_USER).all())
    if stale:
        for d in stale:
            db.session.delete(d)
        db.session.commit()

    return jsonify({"ok": True}), 200


@bp.route("/api/profile/push-device", methods=["DELETE"])
@jwt_required()
def unregister_push_device():
    """Called by the app on logout, before the session is cleared, so a
    shared phone stops receiving the previous user's chats. Only removes the
    id if it belongs to the caller."""
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    if not _PLAYER_ID_RE.match(player_id):
        return jsonify({"error": "Invalid player_id"}), 400
    PushDevice.query.filter_by(player_id=player_id, user_id=user.id).delete(
        synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/api/users/<uid>", methods=["GET"])
def get_public_user(uid):
    """Public, minimal profile lookup — used by the chat window to show
    the other party's name/avatar."""
    user = User.query.get(uid)
    if not user:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": user.id, "first_name": user.first_name, "last_name": user.last_name,
        "avatar_url": user.avatar_url,
        # Shown as "Member since ..." on the product page. Deliberately limited:
        # no email or phone here, since this endpoint is public.
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "city": user.city
    })
