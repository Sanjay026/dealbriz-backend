from datetime import datetime

from extensions import db
from .base import iso_utc


class SiteSetting(db.Model):
    """Key/value feature flags. Generic on purpose so a new switch is a row,
    not a migration."""
    __tablename__ = "site_settings"
    setting_key = db.Column(db.String(60), primary_key=True)
    setting_value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))

    def to_dict(self):
        return {
            "key": self.setting_key,
            "value": self.setting_value,
            "updated_at": iso_utc(self.updated_at),
        }
