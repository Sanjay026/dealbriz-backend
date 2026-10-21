from datetime import datetime

from extensions import db


class Role(db.Model):
    __tablename__ = "roles"
    role_id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Admin(db.Model):
    """Links a user to a staff role. A user is expected to hold at most one
    row with is_active=True at a time — assigning a new role deactivates the
    previous one rather than stacking (see admin/routes.py)."""
    __tablename__ = "admin"
    admin_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.role_id"), nullable=False)
    assigned_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    is_active = db.Column(db.Boolean, default=True)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
