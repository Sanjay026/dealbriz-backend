from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.String(36), primary_key=True, default=gen_id)
    product_id = db.Column(db.String(36), db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sender_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # DealLock: "text" for normal chat, "offer" for an offer card (offer_id
    # says which), "system" for deal updates (locked, released, ...).
    # body is always readable text, so a client that predates these columns
    # still shows something sensible.
    kind = db.Column(db.String(20), nullable=False, default="text")
    offer_id = db.Column(db.String(36), db.ForeignKey("deal_offers.id", ondelete="SET NULL"))

    def to_dict(self):
        return {
            "id": self.id, "product_id": self.product_id,
            "sender_id": self.sender_id, "receiver_id": self.receiver_id,
            "body": self.body, "is_read": self.is_read,
            "kind": self.kind or "text", "offer_id": self.offer_id,
            "created_at": iso_utc(self.created_at)
        }
