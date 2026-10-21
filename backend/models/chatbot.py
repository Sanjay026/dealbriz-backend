from datetime import datetime

from extensions import db
from .base import iso_utc


class ChatbotUnanswered(db.Model):
    """A question the bot couldn't answer.

    Deduplicated on the normalised text so 40 people asking the same thing
    is one row with ask_count=40, not 40 rows — which is what makes the
    list actionable rather than just long.
    """
    __tablename__ = "chatbot_unanswered"
    id = db.Column(db.String(36), primary_key=True)
    question = db.Column(db.String(500), nullable=False)
    normalised = db.Column(db.String(500), nullable=False, unique=True)
    ask_count = db.Column(db.Integer, nullable=False, default=1)
    best_score = db.Column(db.Numeric(6, 2))
    is_resolved = db.Column(db.Boolean, nullable=False, default=False)
    first_asked = db.Column(db.DateTime, default=datetime.utcnow)
    last_asked = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "ask_count": self.ask_count,
            "best_score": float(self.best_score) if self.best_score is not None else None,
            "is_resolved": bool(self.is_resolved),
            "last_asked": iso_utc(self.last_asked),
        }
