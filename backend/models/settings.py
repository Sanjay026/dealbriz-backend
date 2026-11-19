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


class AppSetting(db.Model):
    """Admin -> Settings -> App settings (ddb/migration_app_settings.sql).
    A small table edited from the admin panel: id, text, key, value,
    version, message. Imported as models.settings.AppSetting."""
    __tablename__ = "app_settings"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    text = db.Column(db.String(100), nullable=False)
    setting_key = db.Column(db.String(60), nullable=False, unique=True)
    value = db.Column(db.String(500), nullable=False, default="0")
    version = db.Column(db.Integer)
    message = db.Column(db.String(255))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(36))

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "key": self.setting_key,
            "value": self.value,
            "version": self.version,
            "message": self.message,
            "updated_at": iso_utc(self.updated_at),
        }
