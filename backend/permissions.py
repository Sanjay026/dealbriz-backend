"""Role-based permissions.

Before this, `has_staff_access()` returned True for admin, manager AND
superadmin alike — so a "manager" could delete users and wipe listings just
like a superadmin. The role names existed but carried no actual authority.

This module makes the roles mean something. Each role maps to an explicit
set of capabilities, and endpoints ask for a capability rather than asking
"is this person staff?".

Design rules worth keeping:

- Capabilities are additive. A user with two roles gets the union.
- Anything destructive or trust-related (deleting accounts, granting roles,
  editing legal text, toggling EMI) is superadmin-only. Those are the
  actions you cannot undo from the UI.
- Unknown role -> no capabilities. A typo in the database must not
  accidentally grant access.
"""
from functools import wraps

from flask import jsonify
from flask_jwt_extended import jwt_required

# ---- Capability vocabulary -------------------------------------------
# Grouped by the admin section they unlock, so the sidebar and the API
# agree on what a role can reach.
CAPABILITIES = {
    # Dashboard / analytics
    "dashboard.view",

    # EMI applications
    "emi.view", "emi.review", "emi.delete",

    # Users
    "users.view", "users.suspend", "users.delete", "users.assign_role",

    # Listings
    "products.view", "products.edit", "products.delete",

    # Purchases
    "purchases.view",

    # DealLock deals. view = the staff queue; manage = the lender-side steps
    # done by hand until a lender has an API (Ready to pay, Paid with UTR,
    # resolving disputes). manage moves money-related state, so it is
    # admin and superadmin only.
    "deals.view", "deals.manage",

    # Lender portal: a lender's (NBFC's) own people work their own loans and
    # deals there. Scoped to the one lender the user is linked to
    # (financer_members), so a lender never sees another lender's work.
    "lender.portal",

    # Financing partners
    "financers.view", "financers.manage",

    # Reports queue
    "reports.view", "reports.resolve",

    # Content
    "faqs.manage", "blog.manage", "legal.edit",

    # Platform switches
    "settings.manage",

    # Diagnostics. Separate from settings.manage because reading logs and
    # changing platform behaviour are different jobs: a developer needs the
    # first to debug, and shouldn't need the second to do it.
    "logs.view",
}

ROLE_CAPABILITIES = {
    # Everything, including the irreversible actions.
    "superadmin": set(CAPABILITIES),

    # Runs the marketplace day to day. Deliberately CANNOT: delete user
    # accounts, grant roles, edit legal documents, or turn EMI on/off —
    # those change the platform itself rather than operate it.
    "admin": {
        "dashboard.view",
        "emi.view", "emi.review", "emi.delete",
        "users.view", "users.suspend",
        "products.view", "products.edit", "products.delete",
        "purchases.view",
        "deals.view", "deals.manage",
        "financers.view", "financers.manage",
        "reports.view", "reports.resolve",
        "faqs.manage", "blog.manage",
    },

    # Front-line moderation. Can review and act on queues, but cannot
    # delete anything permanently or touch partners and content.
    "manager": {
        "dashboard.view",
        "emi.view", "emi.review",
        "users.view",
        "products.view", "products.edit",
        "purchases.view",
        "deals.view",
        "financers.view",
        "reports.view", "reports.resolve",
    },

    # Developer / on-call. Sees diagnostics and enough read-only context to
    # reproduce a bug, but cannot change data: debugging a problem should
    # never require the ability to delete the evidence.
    "dev": {
        "dashboard.view",
        "logs.view",
        "emi.view",
        "users.view",
        "products.view",
        "purchases.view",
        "reports.view",
    },

    # A seller privilege, not a staff role — no admin panel at all.
    "merchant": set(),

    # Lender (NBFC) staff. Only their own lender's loans, deals and seller
    # documents, in the Lender portal tab. A superadmin can grant more in
    # Roles and permissions, but anything outside the portal is NOT
    # limited to their lender, so grant with care.
    "lender": {"lender.portal"},
}

# Sections the admin sidebar shows, and the capability that unlocks each.
# The frontend reads this via /auth/me so the menu and the API can never
# disagree about who may see what.
SECTION_CAPABILITY = {
    "dashboard": "dashboard.view",
    "emi": "emi.view",
    "purchases": "purchases.view",
    "deals": "deals.view",
    "lender": "lender.portal",
    "financers": "financers.view",
    "products": "products.view",
    "users": "users.view",
    "reports": "reports.view",
    "settings": "settings.manage",
    # Superadmin-only in practice: the endpoints refuse anyone else.
    "roles": "users.assign_role",
    "emiareas": "settings.manage",
    "faqs": "faqs.manage",
    "chatbot": "faqs.manage",
    "logs": "logs.view",
    "blog": "blog.manage",
    "terms": "legal.edit",
}


# ---------------------------------------------------------------------
# Runtime overrides
#
# ROLE_CAPABILITIES above are the DEFAULTS. A superadmin can override them
# per role from the admin panel; those overrides live in role_permissions.
#
# Cached in-process because this runs on every authenticated request —
# a database round trip per permission check would be a real cost. The
# cache is cleared whenever the table is written, so an edit takes effect
# on the next request in that worker. Gunicorn runs several workers, each
# with its own copy, so the cache also expires after CACHE_SECONDS: an
# edit reaches every worker within that time instead of only the worker
# that saved it (which used to need a restart).
# ---------------------------------------------------------------------
import time as _time

CACHE_SECONDS = 15
_override_cache = None
_override_loaded_at = 0.0


def clear_permission_cache():
    global _override_cache
    _override_cache = None


def _load_overrides():
    """{role_name: set(capabilities)} for roles that have been configured.
    Roles absent from this map fall back to ROLE_CAPABILITIES."""
    global _override_cache, _override_loaded_at
    if _override_cache is not None and (_time.monotonic() - _override_loaded_at) < CACHE_SECONDS:
        return _override_cache
    try:
        from models import RolePermission, RolePermissionState
        configured = {r.role_name for r in RolePermissionState.query.filter_by(configured=True).all()}
        grants = {}
        for row in RolePermission.query.all():
            grants.setdefault(row.role_name, set()).add(row.capability)
        # A configured role with no grants maps to an empty set — that's a
        # deliberate "this role can see nothing", not a missing config.
        _override_cache = {r: grants.get(r, set()) for r in configured}
    except Exception:
        # Before the migration runs, or if the table is unreachable, fall
        # back to defaults rather than locking everyone out.
        _override_cache = {}
    _override_loaded_at = _time.monotonic()
    return _override_cache


def effective_role_capabilities(role_name):
    """What a role can do right now: its override if configured, else the
    built-in default.

    superadmin is never overridable. Allowing it would let a superadmin
    remove their own ability to assign roles and permanently lock the
    platform's administration — an unrecoverable state from the UI.
    """
    if role_name == "superadmin":
        return set(ROLE_CAPABILITIES["superadmin"])
    overrides = _load_overrides()
    if role_name in overrides:
        return set(overrides[role_name])
    return set(ROLE_CAPABILITIES.get(role_name, set()))


def capabilities_for_roles(role_names):
    caps = set()
    for r in role_names or []:
        caps |= effective_role_capabilities(r)
    return caps


def user_capabilities(user):
    """All capabilities for a user, from every active role they hold."""
    if not user:
        return set()

    from utils import get_user_role_names
    roles = get_user_role_names(user.id)
    caps = capabilities_for_roles(roles)

    # Backward compatibility: accounts flagged is_admin before the roles
    # table existed still work. Treated as "admin", NOT superadmin — an
    # old flag shouldn't silently confer the power to delete accounts or
    # rewrite the Terms of Service.
    if not caps and getattr(user, "is_admin", False):
        caps = effective_role_capabilities("admin")
    return caps


def has_capability(user, capability):
    return capability in user_capabilities(user)


def allowed_sections(user):
    caps = user_capabilities(user)
    return [s for s, cap in SECTION_CAPABILITY.items() if cap in caps]


def require_capability(capability):
    """Endpoint guard. Use instead of an inline has_staff_access() check so
    the required permission is visible in the route definition itself.

        @bp.route("/users/<uid>", methods=["DELETE"])
        @require_capability("users.delete")
        def delete_user(uid): ...

    Implies @jwt_required — there's no case where we want a capability
    check without an authenticated caller.
    """
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            from utils import current_user
            user = current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if not has_capability(user, capability):
                return jsonify({
                    "error": "You don't have permission to do that.",
                    "required": capability,
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator
