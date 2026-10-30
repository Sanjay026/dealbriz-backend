from datetime import datetime

from extensions import db


class ProductEvent(db.Model):
    """One visitor action, for Admin -> Analytics.

    event_type:
      impression  a listing card was shown on screen (CTR denominator)
      click       a listing was opened (CTR numerator)
      dwell       time spent looking at an open listing (dwell_ms)
      category    a category was browsed (categories explored)
    No raw IP is stored: ip_hash is an HMAC of it with a server key, so it
    can count unique visitors but can't be reversed or looked up."""
    __tablename__ = "product_events"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    event_type = db.Column(db.String(16), nullable=False)
    product_id = db.Column(db.String(36), index=True)
    category = db.Column(db.String(30), index=True)
    dwell_ms = db.Column(db.Integer)
    source = db.Column(db.String(20))
    session_id = db.Column(db.String(64))
    user_id = db.Column(db.String(36))
    device_type = db.Column(db.String(10))
    ip_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index("ix_product_events_type_time", "event_type", "created_at"),
    )
