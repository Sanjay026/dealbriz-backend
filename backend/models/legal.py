from datetime import datetime

from extensions import db
from .base import iso_utc


class LegalDocument(db.Model):
    """Generic legal-page storage (Terms of Service today; Privacy Policy or
    others could reuse this table later via a different doc_key)."""
    __tablename__ = "legal_documents"
    doc_key = db.Column(db.String(30), primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))

    def to_dict(self):
        return {
            "doc_key": self.doc_key,
            "title": self.title,
            "content": self.content,
            "updated_at": iso_utc(self.updated_at),
            "updated_by": self.updated_by,
        }
