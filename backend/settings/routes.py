from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import SiteSetting
from settings_util import DEFAULT_SETTINGS, all_public_settings, _truthy
from utils import current_user, is_superadmin

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# Flags a superadmin may toggle from the admin panel.
EDITABLE_SETTINGS = {"emi_enabled", "emi_status_enabled"}


@bp.route("", methods=["GET"])
def get_settings():
    """Public — the frontend reads this on boot to decide what to render.
    Only whitelisted flags are returned."""
    return jsonify(all_public_settings())


@bp.route("/<key>", methods=["PUT"])
@jwt_required()
def update_setting(key):
    """Superadmin only. Turning EMI on or off changes what the whole public
    site offers, so it gets the same gate as editing legal documents rather
    than general staff access."""
    user = current_user()
    if not is_superadmin(user):
        return jsonify({"error": "Only superadmins can change site settings"}), 403
    if key not in EDITABLE_SETTINGS:
        return jsonify({"error": "Unknown setting"}), 400

    data = request.get_json() or {}
    if "value" not in data:
        return jsonify({"error": "A value is required"}), 400
    value = "true" if _truthy(data["value"]) else "false"

    row = SiteSetting.query.get(key)
    if not row:
        row = SiteSetting(setting_key=key, setting_value=value)
        db.session.add(row)
    else:
        row.setting_value = value
    row.updated_by = user.id
    db.session.commit()
    return jsonify(all_public_settings())


# ===================== ANDROID APP VERSION (force update) =====================
# Read by the app on launch and whenever it comes back to the foreground.
#   build < ANDROID_MIN_VERSION_CODE     -> blocking "Update required" screen
#   build < ANDROID_LATEST_VERSION_CODE  -> dismissible "Update available"
# Values live in ~/deal_briz/.env (recreate the backend container after a
# change). Unset or 0 = no check, so a fresh deploy changes nothing.

def _env_int(name):
    import os
    try:
        return max(0, int(str(os.environ.get(name, "0")).strip() or 0))
    except ValueError:
        return 0


@bp.route("/app-version", methods=["GET"])
def app_version():
    """Public, no login: an outdated app must be able to ask before sign-in."""
    import os
    url = str(os.environ.get("ANDROID_UPDATE_URL", "")).strip()
    # Only a web link: the app opens it outside itself, and a stray scheme
    # (javascript:, intent:) has no business there.
    if not url.startswith("https://"):
        url = ""
    message = str(os.environ.get("ANDROID_UPDATE_MESSAGE", "")).strip()[:300]
    resp = jsonify({
        "android": {
            "min_version_code": _env_int("ANDROID_MIN_VERSION_CODE"),
            "latest_version_code": _env_int("ANDROID_LATEST_VERSION_CODE"),
            "update_url": url or None,
            "message": message or None,
        }
    })
    # Raised the moment a release goes out, so never cached.
    resp.headers["Cache-Control"] = "no-store"
    return resp
