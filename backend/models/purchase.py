from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class Purchase(db.Model):
    __tablename__ = "purchase"
    purchase_id = db.Column(db.String(36), primary_key=True, default=gen_id)
    product_id = db.Column(db.String(36), db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    financer_id = db.Column(db.Integer, db.ForeignKey("financers.financer_id", ondelete="SET NULL"))
    emi_application_id = db.Column(db.String(36), db.ForeignKey("emi_applications.id", ondelete="SET NULL"))
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(20), default="full_payment")
    status = db.Column(db.String(20), default="pending")
    purchase_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "purchase_id": self.purchase_id, "product_id": self.product_id,
            "financer_id": self.financer_id, "emi_application_id": self.emi_application_id,
            "total_amount": float(self.total_amount), "payment_method": self.payment_method,
            "status": self.status,
            "purchase_date": iso_utc(self.purchase_date)
        }


class PurchaseUser(db.Model):
    """Junction table: one row per participant in a purchase, so the same
    purchase carries both its buyer and its seller."""
    __tablename__ = "purchase_user"
    purchase_user_id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.String(36), db.ForeignKey("purchase.purchase_id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_in_purchase = db.Column(db.String(20), nullable=False)  # 'buyer' or 'seller'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
