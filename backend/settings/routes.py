from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import SiteSetting
from settings_util import DEFAULT_SETTINGS, all_public_settings, _truthy
from permissions import require_capability
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
    """Public, no login: an outdated app must be able to ask before sign-in.

    Admin -> Settings -> App settings ("Update app", app_settings table)
    decides when its row exists:
        value 0  no prompt for anyone
        value 1  builds below "version" get the dismissible "Update available"
        value 2  builds below "version" get the blocking "Update required"
    The response keeps the same two numbers the app already reads
    (min_version_code / latest_version_code), so apps already installed
    follow the admin setting without an update. Without the row (migration
    not run yet) the .env values apply exactly as before."""
    import os
    url = str(os.environ.get("ANDROID_UPDATE_URL", "")).strip()
    # Only a web link: the app opens it outside itself, and a stray scheme
    # (javascript:, intent:) has no business there.
    if not url.startswith("https://"):
        url = ""
    message = str(os.environ.get("ANDROID_UPDATE_MESSAGE", "")).strip()[:300]
    min_code = _env_int("ANDROID_MIN_VERSION_CODE")
    latest_code = _env_int("ANDROID_LATEST_VERSION_CODE")
    mode = None
    row = _app_row(APP_UPDATE_KEY)
    if row is not None:
        mode = _update_mode(row.value)
        latest = int(row.version or 0)
        min_code = latest if (mode == 2 and latest > 0) else 0
        latest_code = latest if (mode in (1, 2) and latest > 0) else 0
    resp = jsonify({
        "android": {
            "min_version_code": min_code,
            "latest_version_code": latest_code,
            "update_url": url or None,
            "message": message or None,
            # Additive: 0/1/2 from Admin -> App settings (null without the table).
            "update_mode": mode,
        }
    })
    # Raised the moment a release goes out, so never cached.
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ===================== APP SETTINGS (Admin -> Settings -> App settings) =====================
# The app_settings table (ddb/migration_app_settings.sql): id, text, key,
# value, version, message. Reading the rows needs the Settings section
# (settings.manage); changing them is superadmin only, like Site settings.
#   app_update      value 0/1/2 + version (the latest versionCode)
#   welcome_screen  value 1 = shown when the app opens, 0 = off
#   welcome_image   value = the uploaded picture's URL ('' = built-in demo)

APP_UPDATE_KEY = "app_update"
WELCOME_SCREEN_KEY = "welcome_screen"
WELCOME_IMAGE_KEY = "welcome_image"
MAX_VERSION_CODE = 2100000000    # Play's own ceiling for versionCode


def _app_row(key):
    """The row, or None when it (or the whole table) isn't there yet. A
    missing table must never break the public endpoints."""
    try:
        from models.settings import AppSetting
        return AppSetting.query.filter_by(setting_key=key).first()
    except Exception:
        db.session.rollback()
        return None


def _update_mode(value):
    try:
        mode = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return mode if mode in (0, 1, 2) else 0


def _all_app_rows():
    from models.settings import AppSetting
    return [r.to_dict() for r in AppSetting.query.order_by(AppSetting.id.asc()).all()]


def _require_superadmin():
    user = current_user()
    if not is_superadmin(user):
        return None, (jsonify({"error": "Only superadmins can change app settings"}), 403)
    return user, None


@bp.route("/app", methods=["GET"])
def public_app_settings():
    """Public, no login: what the app shows on launch (the welcome screen).
    image_url null = the app's built-in demo picture."""
    screen = _app_row(WELCOME_SCREEN_KEY)
    image = _app_row(WELCOME_IMAGE_KEY)
    img = (image.value or "").strip() if image is not None else ""
    resp = jsonify({
        "welcome": {
            # Shown unless an admin turned it off.
            "enabled": (screen is None) or str(screen.value).strip() != "0",
            "image_url": img or None,
            "updated_at": image.to_dict()["updated_at"] if (image is not None and img) else None,
        }
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/app/rows", methods=["GET"])
@require_capability("settings.manage")
def list_app_settings():
    try:
        rows = _all_app_rows()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "The app settings table isn't there yet. Run "
                                 "ddb/migration_app_settings.sql on the database."}), 503
    return jsonify({"items": rows, "can_edit": is_superadmin(current_user())})


@bp.route("/app/<key>", methods=["PUT"])
@jwt_required()
def update_app_setting(key):
    """{value, version?}. app_update: value 0/1/2, version = the latest
    versionCode (needed for 1 and 2). welcome_screen: value 0/1. The
    welcome picture is changed with POST/DELETE /app/welcome-image."""
    user, denied = _require_superadmin()
    if denied:
        return denied
    if key not in (APP_UPDATE_KEY, WELCOME_SCREEN_KEY):
        return jsonify({"error": "Unknown app setting"}), 400
    row = _app_row(key)
    if row is None:
        return jsonify({"error": "This setting isn't in the database yet. Run "
                                 "ddb/migration_app_settings.sql first."}), 409
    data = request.get_json(silent=True) or {}
    raw = str(data.get("value", "")).strip()

    if key == APP_UPDATE_KEY:
        if raw not in ("0", "1", "2"):
            return jsonify({"error": "Value must be 0, 1 or 2."}), 400
        version = row.version
        if "version" in data and data.get("version") not in (None, ""):
            try:
                version = int(str(data.get("version")).strip())
            except (TypeError, ValueError):
                return jsonify({"error": "Version must be a whole number (the app's versionCode, e.g. 18)."}), 400
            if version < 1 or version > MAX_VERSION_CODE:
                return jsonify({"error": "Version must be a versionCode from 1 upwards."}), 400
        if raw in ("1", "2") and not version:
            return jsonify({"error": "Enter the latest version (versionCode) first."}), 400
        row.value = raw
        row.version = version
    else:
        if raw not in ("0", "1"):
            return jsonify({"error": "Value must be 0 (off) or 1 (on)."}), 400
        row.value = raw

    from datetime import datetime as _dt
    row.updated_by = user.id
    row.updated_at = _dt.utcnow()
    db.session.commit()
    return jsonify({"items": _all_app_rows(), "can_edit": True})


@bp.route("/app/welcome-image", methods=["POST"])
@jwt_required()
def upload_welcome_image():
    """multipart "file": a JPG, PNG or WEBP picture for the app's welcome
    screen. Replaces the previous one."""
    import uuid
    from flask import current_app
    from storage import check_image, delete_upload, save_upload
    user, denied = _require_superadmin()
    if denied:
        return denied
    row = _app_row(WELCOME_IMAGE_KEY)
    if row is None:
        return jsonify({"error": "This setting isn't in the database yet. Run "
                                 "ddb/migration_app_settings.sql first."}), 409
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a picture to upload."}), 400
    ext, content_type, problem = check_image(file, allowed=("jpg", "png", "webp"))
    if problem:
        return jsonify({"error": problem}), 400
    try:
        url = save_upload(file, f"welcome_{uuid.uuid4().hex}.{ext}", content_type)
    except Exception:
        current_app.logger.error("Welcome picture upload failed", exc_info=True)
        return jsonify({"error": "Could not save that picture. Please try again."}), 500
    from datetime import datetime as _dt
    old = (row.value or "").strip()
    row.value = url[:500]
    row.updated_by = user.id
    row.updated_at = _dt.utcnow()
    db.session.commit()
    if old and old != url:
        delete_upload(old)
    return jsonify({"items": _all_app_rows(), "can_edit": True}), 201


@bp.route("/app/welcome-image", methods=["DELETE"])
@jwt_required()
def reset_welcome_image():
    """Back to the app's built-in demo picture."""
    from storage import delete_upload
    user, denied = _require_superadmin()
    if denied:
        return denied
    row = _app_row(WELCOME_IMAGE_KEY)
    if row is None:
        return jsonify({"error": "This setting isn't in the database yet. Run "
                                 "ddb/migration_app_settings.sql first."}), 409
    from datetime import datetime as _dt
    old = (row.value or "").strip()
    row.value = ""
    row.updated_by = user.id
    row.updated_at = _dt.utcnow()
    db.session.commit()
    if old:
        delete_upload(old)
    return jsonify({"items": _all_app_rows(), "can_edit": True})
