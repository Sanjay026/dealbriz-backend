from datetime import datetime

from extensions import db
from .base import iso_utc


class DocumentConsent(db.Model):
    """One tick of "I agree ... Privacy Policy" before uploading documents
    (ddb/migration_deal_done_check.sql). context says where: "deal:<id>",
    "listing:<id>" or "sell". Imported as models.consent.DocumentConsent."""
    __tablename__ = "document_consents"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), nullable=False)
    context = db.Column(db.String(80))
    policy = db.Column(db.String(40))
    consented_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ip = db.Column(db.String(45))

    def to_dict(self):
        return {
            "id": self.id,
            "context": self.context,
            "policy": self.policy,
            "consented_at": iso_utc(self.consented_at),
        }
