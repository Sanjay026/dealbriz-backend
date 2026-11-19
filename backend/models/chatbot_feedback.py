from datetime import datetime

from extensions import db
from .base import iso_utc


class ChatbotQuestion(db.Model):
    """A question someone asked the support chatbot and then rated the
    answer to (table 1). id counts up 1, 2, 3...

    The same question asked again ("How do I sell?", "how do i sell ??")
    is the same row: text_hash is a SHA-256 of the normalised text
    (lower case, punctuation and extra spaces removed), unique, so a
    question is stored once however many people ask it. text keeps the
    wording of the first person who asked.
    """
    __tablename__ = "chatbot_questions"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    text = db.Column(db.String(500), nullable=False)
    text_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "text": self.text, "created_at": iso_utc(self.created_at)}


class ChatbotFeedback(db.Model):
    """One person's thumbs up / thumbs down on the answer to one question
    (table 2).

    One row per (question, person): asking the same question again and
    rating again REPLACES the row, so only the last review is kept. likes and
    dislikes hold that last review: 1/0 for a thumbs up, 0/1 for a thumbs
    down. Totals for a question are the sums over its rows.

    The person is user_id when signed in, else guest_key (a random id the
    browser keeps). voter_key is "u:<user id>" or "g:<guest key>", unique per
    question, so a guest and a member never collide and a NULL user_id can't
    slip past the unique index.

    answer_text / answer_source: the answer that was rated (signed by the
    server when it was given, so a client can't make up an answer here).
    The AI reads the disliked ones before answering the same question again.
    reason: optional, why a thumbs down (chatbot/feedback.py REASONS).
    support_report_id: the "Contact support" request this person sent from
    this answer (a problem report in Admin -> Reports).
    """
    __tablename__ = "chatbot_feedback"
    __table_args__ = (db.UniqueConstraint("question_id", "voter_key", name="uq_chatbot_feedback_voter"),)
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question_id = db.Column(db.Integer, db.ForeignKey("chatbot_questions.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    user_id = db.Column(db.String(36), index=True)
    guest_key = db.Column(db.String(36))
    voter_key = db.Column(db.String(40), nullable=False)
    likes = db.Column(db.SmallInteger, nullable=False, default=0)
    dislikes = db.Column(db.SmallInteger, nullable=False, default=0)
    answer_text = db.Column(db.Text)
    answer_source = db.Column(db.String(20))
    reason = db.Column(db.String(30))
    support_report_id = db.Column(db.String(36))
    support_requested_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
