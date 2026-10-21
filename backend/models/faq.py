from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class Faq(db.Model):
    """One Help Centre question. Stored per-row (rather than as one big
    document) so staff can reorder, unpublish or edit entries individually
    and the Help Centre can search and group them."""
    __tablename__ = "faqs"
    faq_id = db.Column(db.String(36), primary_key=True, default=gen_id)
    question = db.Column(db.String(300), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    # Alternative phrasings the chatbot should match on. The question text
    # alone isn't enough: people ask "how do I post" when the FAQ says
    # "list an item for sale".
    keywords = db.Column(db.String(400))
    category = db.Column(db.String(50), nullable=False, default="general")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_published = db.Column(db.Boolean, nullable=False, default=True)
    # is_published lets the chatbot answer from this entry. show_in_help
    # decides whether it ALSO appears on the public Help Centre page — a
    # CSV import can add hundreds of answers that should teach the bot
    # without turning that page into an unreadable wall.
    show_in_help = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(20), nullable=False, default="manual")  # manual|import
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "faq_id": self.faq_id,
            "question": self.question,
            "answer": self.answer,
            "keywords": self.keywords,
            "category": self.category,
            "sort_order": self.sort_order,
            "is_published": bool(self.is_published),
            "show_in_help": bool(self.show_in_help),
            "source": self.source,
            "updated_at": iso_utc(self.updated_at),
        }
