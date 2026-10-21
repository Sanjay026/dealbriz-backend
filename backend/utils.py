"""
Shared helpers used across multiple blueprints — mainly the current-user
lookup and staff/role-permission checks. Centralised here so every
blueprint checks permissions the exact same way.
"""
from flask_jwt_extended import get_jwt_identity

from extensions import db
from models import User, Role, Admin


def current_user():
    uid = get_jwt_identity()
    return User.query.get(uid)


def get_user_role_names(user_id):
    """Returns a list of active role names (e.g. ['superadmin']) for a user.
    Defensive: if the roles/admin tables have any issue, fail gracefully
    with an empty list rather than crashing the whole request (this used
    to break /auth/me — and therefore every profile-related page — for
    ALL users whenever the roles system had any problem)."""
    try:
        rows = (
            db.session.query(Role.role_name)
            .join(Admin, Admin.role_id == Role.role_id)
            .filter(Admin.user_id == user_id, Admin.is_active == True)
            .all()
        )
        return [r[0] for r in rows]
    except Exception as e:
        print(f"[warning] get_user_role_names failed for user {user_id}: {e}")
        db.session.rollback()
        return []


def has_staff_access(user):
    """True if user has ANY staff role (admin/manager/superadmin) — not merchant."""
    if not user:
        return False
    if user.is_admin:  # backward-compat fallback for accounts set up before the roles table
        return True
    roles = get_user_role_names(user.id)
    return any(r in ("admin", "manager", "superadmin") for r in roles)


def is_superadmin(user):
    if not user:
        return False
    roles = get_user_role_names(user.id)
    return "superadmin" in roles
