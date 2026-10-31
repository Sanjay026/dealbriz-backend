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


class ProductView(db.Model):
    """One row per person who viewed a listing: the unique view count.

    viewer_key: "u:<user id>" for a signed-in person, or "a:<hash>" for a
    visitor who isn't signed in (keyed hash of IP + browser, no raw IP).
    The unique key makes a second view by the same person a no-op, so
    products.views only goes up the first time."""
    __tablename__ = "product_views"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    product_id = db.Column(db.String(36), nullable=False)
    viewer_key = db.Column(db.String(80), nullable=False)
    first_viewed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    last_viewed_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("product_id", "viewer_key", name="uq_product_views_viewer"),
        db.Index("ix_product_views_product_time", "product_id", "first_viewed_at"),
    )
