from datetime import datetime

from extensions import db
from .base import iso_utc


class Pincode(db.Model):
    """India Post directory. One pincode can host several post offices, so
    the primary key is (pincode, office_name)."""
    __tablename__ = "pincodes"
    pincode = db.Column(db.String(6), primary_key=True)
    office_name = db.Column(db.String(120), primary_key=True)
    district = db.Column(db.String(80), nullable=False)
    state = db.Column(db.String(80), nullable=False)
    latitude = db.Column(db.Numeric(9, 6))
    longitude = db.Column(db.Numeric(9, 6))
    is_major = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {
            "pincode": self.pincode,
            "office_name": self.office_name,
            "district": self.district,
            "state": self.state,
            "latitude": float(self.latitude) if self.latitude is not None else None,
            "longitude": float(self.longitude) if self.longitude is not None else None,
            "is_major": bool(self.is_major),
        }


class EmiServiceArea(db.Model):
    """Where EMI is offered. Checked most-specific-first (pincode beats
    district beats state), so a whole state can be enabled and individual
    areas carved out of it."""
    __tablename__ = "emi_service_areas"
    area_id = db.Column(db.String(36), primary_key=True)
    scope_type = db.Column(db.String(20), nullable=False)   # state|district|pincode
    scope_value = db.Column(db.String(80), nullable=False)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    financer_id = db.Column(db.Integer, db.ForeignKey("financers.financer_id", ondelete="SET NULL"))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))

    def to_dict(self):
        return {
            "area_id": self.area_id,
            "scope_type": self.scope_type,
            "scope_value": self.scope_value,
            "is_enabled": bool(self.is_enabled),
            "financer_id": self.financer_id,
            "note": self.note,
            "updated_at": iso_utc(self.updated_at),
        }
