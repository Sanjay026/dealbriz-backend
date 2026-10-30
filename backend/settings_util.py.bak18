"""Shared helpers for reading site settings.

Lives outside any blueprint because both the settings API and the EMI
endpoints need it — the whole point of the flag is that EMI routes refuse
to run when it's off, so the check can't live inside the settings module
alone.
"""
from models import SiteSetting

# Every flag the app understands, with the value used when the row is
# missing entirely (e.g. the migration hasn't run yet).
DEFAULT_SETTINGS = {
    # Safe default: EMI stays OFF until a financing partner is signed and
    # someone explicitly switches it on.
    "emi_enabled": "false",
}


def _truthy(value):
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def get_setting(key):
    row = SiteSetting.query.get(key)
    if row:
        return row.setting_value
    return DEFAULT_SETTINGS.get(key)


def get_bool_setting(key):
    return _truthy(get_setting(key))


def emi_enabled():
    return get_bool_setting("emi_enabled")


def all_public_settings():
    """Flags the frontend is allowed to read without authentication.
    Whitelisted rather than dumping the table, so an internal-only flag
    added later isn't exposed by accident."""
    return {"emi_enabled": emi_enabled()}
