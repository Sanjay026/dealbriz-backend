from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class Notification(db.Model):
    """A message addressed to one user, shown under the navbar bell.

    Deliberately a stored row rather than something derived on the fly from
    EMI/report/listing state. Derived notifications have no place to record
    "the user has seen this", so the bell either never clears or clears
    everything at once — and a status that changes twice (review -> approved
    -> disbursed) collapses into a single item that loses the middle step.
    """
    __tablename__ = "notifications"
    id = db.Column(db.String(36), primary_key=True, default=gen_id)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    # Drives the icon and tint in the dropdown: emi | chat | listing | system.
    kind = db.Column(db.String(30), nullable=False, default="system")
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    # Where clicking the row should take the user. link_page is passed
    # straight to showPage() in app.js, so it must be a real page id.
    link_page = db.Column(db.String(50))
    link_ref = db.Column(db.String(36))
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    @classmethod
    def push(cls, user_id, kind, title, body=None, link_page=None, link_ref=None):
        """Queue a notification on the current session. Does NOT commit — the
        caller commits, so the notification and the change it announces land
        in the same transaction. A notification saying "your EMI was
        approved" must not survive a rolled-back approval.

        Returns None when there is no recipient (e.g. an EMI row whose user
        was deleted), so callers never need to null-check first.
        """
        if not user_id:
            return None
        n = cls(user_id=user_id, kind=kind, title=title[:200],
                body=body, link_page=link_page, link_ref=link_ref)
        db.session.add(n)
        return n

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "link_page": self.link_page,
            "link_ref": self.link_ref,
            "is_read": bool(self.is_read),
            "created_at": iso_utc(self.created_at),
        }
