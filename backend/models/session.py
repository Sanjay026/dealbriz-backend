from datetime import datetime

from extensions import db
from .base import iso_utc


class UserSession(db.Model):
    """One row per issued login token, so a token can be revoked before it
    expires. Only a HASH of the token id is stored — a leak of this table
    must not hand anyone a working session."""
    __tablename__ = "user_sessions"
    session_id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_info = db.Column(db.String(255))
    ip_address = db.Column(db.String(45))
    jwt_token_hash = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "device_info": self.device_info,
            "ip_address": self.ip_address,
            "is_active": bool(self.is_active),
            "created_at": iso_utc(self.created_at),
            "expires_at": iso_utc(self.expires_at),
        }
