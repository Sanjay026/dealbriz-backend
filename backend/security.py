"""JWT hardening.

The endpoint audit showed authentication was already applied everywhere it
should be, so this closes the two remaining gaps:

1. **Logout did nothing to the token.** Tokens are stateless and were valid
   for their full 30 days regardless. Someone who copied a token — from a
   shared machine, a stolen laptop, a leaked log — kept access even after
   the real user "logged out". Tokens are now revocable.

2. **Weak secrets shipped silently.** config.py falls back to
   "dealbridge-secret-key" if the environment doesn't set one. Anyone who
   knows that string can forge a token for any account, including a
   superadmin. That now fails loudly instead of running quietly.
"""
import hashlib
import logging
import os
from datetime import datetime

from flask_jwt_extended import get_jwt

from extensions import db

log = logging.getLogger("dealbridge.security")

# The placeholder values in config.py. Running with these in production is
# equivalent to publishing the signing key.
INSECURE_DEFAULTS = {
    "dealbridge-secret-key",
    "dealbridge-jwt-secret",
    "replace-with-a-long-random-value",
    "replace-with-a-different-long-random-value",
    "change-me",
}

MIN_SECRET_LENGTH = 32


def check_secrets(app):
    """Refuses to boot with a known-weak signing key when not in debug.

    Deliberately fatal rather than a warning: a warning in a startup log is
    read once and then never again, and the failure mode here is silent,
    total account compromise.
    """
    problems = []
    for key in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = str(app.config.get(key) or "")
        if value in INSECURE_DEFAULTS:
            problems.append(f"{key} is still the placeholder value")
        elif len(value) < MIN_SECRET_LENGTH:
            problems.append(f"{key} is only {len(value)} chars (minimum {MIN_SECRET_LENGTH})")

    if app.config.get("SECRET_KEY") and app.config.get("SECRET_KEY") == app.config.get("JWT_SECRET_KEY"):
        problems.append("SECRET_KEY and JWT_SECRET_KEY are identical — they must differ")

    if not problems:
        log.info("Secret key check passed.")
        return

    message = "Insecure configuration: " + "; ".join(problems)
    # FLASK_DEBUG is set for local development; there, warn so nobody is
    # blocked from working, but never let this pass in a real deployment.
    if os.environ.get("FLASK_DEBUG") or app.debug:
        log.warning("%s — allowed because debug mode is on. FIX BEFORE DEPLOYING.", message)
        return

    log.critical(message)
    raise RuntimeError(
        message
        + "\n\nGenerate values with:\n"
        "  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "and set SECRET_KEY and JWT_SECRET_KEY in backend/.env"
    )


def _token_hash(jti):
    """Only a hash of the token id is stored. If the sessions table ever
    leaked, the rows would not be usable as tokens."""
    return hashlib.sha256(jti.encode()).hexdigest()


def register_jwt_handlers(app, jwt):
    from models import UserSession

    @jwt.token_in_blocklist_loader
    def is_token_revoked(jwt_header, jwt_payload):
        """Runs on every authenticated request. A revoked session means the
        token stops working immediately, not in 30 days."""
        # An access token carries "sid" — the id of the refresh token that
        # issued it — so revoking a session invalidates both at once. A
        # refresh token is checked by its own jti.
        jti = jwt_payload.get("sid") or jwt_payload.get("jti")
        if not jti:
            return False
        try:
            session = UserSession.query.filter_by(jwt_token_hash=_token_hash(jti)).first()
            # Unknown tokens are allowed through: sessions issued before this
            # feature existed have no row, and rejecting them would log out
            # every current user on deploy. Only an explicit revocation blocks.
            if session is None:
                return False
            if not session.is_active:
                return True
            if session.expires_at and session.expires_at < datetime.utcnow():
                return True
            return False
        except Exception as exc:
            # A database problem must not lock everyone out of the site.
            log.error("Token revocation check failed: %s", exc, exc_info=True)
            return False

    @jwt.revoked_token_loader
    def revoked_response(jwt_header, jwt_payload):
        from flask import jsonify
        return jsonify({"error": "Your session has ended. Please sign in again."}), 401

    @jwt.expired_token_loader
    def expired_response(jwt_header, jwt_payload):
        from flask import jsonify
        return jsonify({"error": "Your session has expired. Please sign in again."}), 401

    @jwt.invalid_token_loader
    def invalid_response(reason):
        from flask import jsonify
        # The reason is logged, not returned: it can describe the signature
        # scheme, which is a hint we don't need to hand out.
        log.warning("Rejected invalid token: %s", reason)
        return jsonify({"error": "Invalid session. Please sign in again."}), 401

    @jwt.unauthorized_loader
    def missing_response(reason):
        from flask import jsonify
        return jsonify({"error": "Authentication required."}), 401


def record_session(user_id, jti, expires_at, request):
    """Stores a session row so the token can later be revoked."""
    from models import UserSession
    import uuid
    try:
        db.session.add(UserSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            jwt_token_hash=_token_hash(jti),
            device_info=str(request.headers.get("User-Agent", ""))[:255],
            ip_address=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:45],
            expires_at=expires_at,
            is_active=True,
        ))
        db.session.commit()
    except Exception as exc:
        # Login must still succeed if session tracking fails — the user is
        # authenticated either way; we've only lost the ability to revoke.
        log.error("Could not record session for %s: %s", user_id, exc, exc_info=True)
        db.session.rollback()


def revoke_session(jti):
    from models import UserSession
    try:
        session = UserSession.query.filter_by(jwt_token_hash=_token_hash(jti)).first()
        if session:
            session.is_active = False
            db.session.commit()
            return True
        return False
    except Exception as exc:
        log.error("Could not revoke session: %s", exc, exc_info=True)
        db.session.rollback()
        return False


def revoke_all_sessions(user_id):
    """Used when a password changes: every other device must be signed out,
    since the old password may be how an attacker got in."""
    from models import UserSession
    try:
        count = UserSession.query.filter_by(user_id=user_id, is_active=True).update({"is_active": False})
        db.session.commit()
        return count
    except Exception as exc:
        log.error("Could not revoke sessions for %s: %s", user_id, exc, exc_info=True)
        db.session.rollback()
        return 0


def current_jti():
    try:
        return get_jwt().get("jti")
    except Exception:
        return None
