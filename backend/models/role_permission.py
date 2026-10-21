from datetime import datetime

from extensions import db


class RolePermission(db.Model):
    """One granted capability for one role. Absence of any row for a role
    means that role uses the built-in defaults."""
    __tablename__ = "role_permissions"
    role_name = db.Column(db.String(50), primary_key=True)
    capability = db.Column(db.String(60), primary_key=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))


class RolePermissionState(db.Model):
    """Records that a role has been configured at all.

    Needed because "granted nothing" and "never configured" both produce
    zero rows in role_permissions, and they must behave differently: the
    first should grant nothing, the second should fall back to defaults.
    """
    __tablename__ = "role_permission_state"
    role_name = db.Column(db.String(50), primary_key=True)
    configured = db.Column(db.Boolean, nullable=False, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
