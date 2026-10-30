from flask import Blueprint, request, jsonify
import re
from datetime import datetime, timedelta

from flask_jwt_extended import (create_access_token, create_refresh_token,
                                decode_token, get_jwt, get_jwt_identity,
                                jwt_required, set_access_cookies,
                                set_refresh_cookies, unset_jwt_cookies)
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db
from models import User
from permissions import allowed_sections, user_capabilities
from security import record_session, revoke_all_sessions, revoke_session
from utils import current_user, get_user_role_names, has_staff_access

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()
    required = ["email", "password", "first_name", "last_name", "phone", "city"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": "Missing required fields"}), 400
    if len(data["password"]) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    # Validate the email shape. Beyond correctness, an unvalidated email is
    # attacker-controlled text that later gets rendered in the admin users
    # table — the frontend escapes it now, but bad data shouldn't reach the
    # database in the first place.
    email = str(data["email"]).strip().lower()
    if not re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}", email) or len(email) > 255:
        return jsonify({"error": "Please enter a valid email address"}), 400

    # Same reasoning for the name fields, which appear on every listing.
    for field in ("first_name", "last_name"):
        value = str(data[field]).strip()
        if len(value) > 80 or re.search(r"[<>]", value):
            return jsonify({"error": f"Please enter a valid {field.replace('_', ' ')}"}), 400
        data[field] = value

    # Phone: client-side checks are a convenience, not a control — a direct
    # POST bypasses them entirely. Ten digits, optional country code, first
    # digit 6-9 (Indian mobile range), and not a single repeated digit.
    phone_digits = re.sub(r"\D", "", str(data.get("phone", "")))
    phone_digits = re.sub(r"^91", "", phone_digits)
    if (len(phone_digits) != 10
            or phone_digits[0] not in "6789"
            or phone_digits == phone_digits[0] * 10):
        return jsonify({"error": "Please enter a valid 10-digit mobile number"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409

    user = User(
        email=email,
        password_hash=generate_password_hash(data["password"]),
        first_name=data["first_name"], last_name=data["last_name"],
        phone=data["phone"], city=data["city"]
    )
    db.session.add(user)
    db.session.commit()
    resp = issue_session(user)
    resp.status_code = 201
    return resp


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email", "").lower()).first()
    if not user or not check_password_hash(user.password_hash, data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401
    # A suspended account keeps all its data but cannot be used.
    if (user.account_status or "active") == "suspended":
        return jsonify({
            "error": "This account has been suspended. "
                     "Contact DealBriz support if you believe this is a mistake."
        }), 403
    return issue_session(user)


def issue_session(user):
    """Signs a user in by setting httpOnly cookies.

    The token is never returned in the JSON body and never touches
    JavaScript — that is the whole point. A script injection anywhere on
    the site can no longer read the session.

    Two tokens: a short-lived access token for normal requests, and a
    long-lived refresh token that only the /auth/refresh route receives
    (JWT_REFRESH_COOKIE_PATH). Narrowing the refresh cookie's path means it
    isn't attached to every ordinary request, so it's exposed far less.
    """
    refresh = create_refresh_token(identity=user.id)
    # Stamp the access token with the refresh token's id. Revoking the
    # session then kills the access token immediately, instead of leaving
    # it valid until it expires — otherwise "log out everywhere" would take
    # up to 30 minutes to actually take effect.
    refresh_jti = decode_token(refresh)["jti"]
    access = create_access_token(identity=user.id,
                                 additional_claims={"sid": refresh_jti})

    # Record the refresh token so it can be revoked — on logout, on
    # password change, or by a superadmin. Without this a leaked token
    # stays valid for its full lifetime no matter what the user does.
    try:
        claims = decode_token(refresh)
        expires_at = (datetime.utcfromtimestamp(claims["exp"]) if claims.get("exp")
                      else datetime.utcnow() + timedelta(days=30))
        record_session(user.id, claims["jti"], expires_at, request)
    except Exception:
        pass  # never block a valid login on session bookkeeping

    resp = jsonify({"user": user.to_dict(), "authenticated": True})
    set_access_cookies(resp, access)
    set_refresh_cookies(resp, refresh)
    return resp


@bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_session():
    """Exchanges a valid refresh token for a new access token.

    The frontend calls this automatically when a request comes back 401,
    so a user is never logged out mid-action just because 30 minutes
    elapsed — while a stolen access token still dies within the hour.
    """
    identity = get_jwt_identity()
    user = User.query.get(identity)
    if not user:
        resp = jsonify({"error": "Session no longer valid"})
        unset_jwt_cookies(resp)
        return resp, 401

    # Carry the session id forward so the new access token is still tied
    # to a revocable session.
    sid = get_jwt().get("jti")
    resp = jsonify({"user": user.to_dict(), "authenticated": True})
    set_access_cookies(resp, create_access_token(identity=user.id,
                                                 additional_claims={"sid": sid}))
    return resp


@bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """Actually invalidates the token, rather than relying on the browser
    to forget it. Matters on shared machines: clearing localStorage does
    nothing about a token already copied elsewhere."""
    jti = get_jwt().get("jti")
    revoked = revoke_session(jti) if jti else False
    # Clearing the cookies is what the browser sees; revoking is what stops
    # a token that was already copied elsewhere. Both are needed.
    resp = jsonify({"ok": True, "revoked": revoked})
    unset_jwt_cookies(resp)
    return resp


@bp.route("/sessions", methods=["GET"])
@jwt_required()
def my_sessions():
    """Lets a user see where they are signed in."""
    from models import UserSession
    rows = (UserSession.query
            .filter_by(user_id=current_user().id, is_active=True)
            .order_by(UserSession.created_at.desc()).limit(20).all())
    return jsonify([r.to_dict() for r in rows])


@bp.route("/logout-all", methods=["POST"])
@jwt_required()
def logout_all():
    """Signs out every device. The action you want after losing a phone."""
    count = revoke_all_sessions(current_user().id)
    resp = jsonify({"ok": True, "sessions_ended": count})
    unset_jwt_cookies(resp)
    return resp


@bp.route("/me", methods=["GET"])
@jwt_required()
def get_me():
    user = current_user()
    if not user:
        return jsonify({"error": "User not found"}), 404
    data = user.to_dict()
    try:
        data["roles"] = get_user_role_names(user.id)
        data["is_staff"] = has_staff_access(user)
        data["capabilities"] = sorted(user_capabilities(user))
        data["sections"] = allowed_sections(user)
    except Exception as e:
        print(f"[warning] roles lookup failed in get_me for user {user.id}: {e}")
        data["roles"] = []
        data["is_staff"] = bool(user.is_admin)
    return jsonify(data)


@bp.route("/change-password", methods=["POST"])
@jwt_required()
def change_password():
    user = current_user()
    data = request.get_json()
    if not check_password_hash(user.password_hash, data.get("current_password", "")):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(data.get("new_password", "")) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400
    user.password_hash = generate_password_hash(data["new_password"])
    db.session.commit()
    return jsonify({"message": "Password updated"})
