from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class Report(db.Model):
    """A user-submitted report against a seller. Reports are evidence, so a
    report outlives the reporter's account (reporter_id is SET NULL, not
    CASCADE) and is never hard-deleted from the admin UI — it's resolved."""
    __tablename__ = "reports"
    report_id = db.Column(db.String(36), primary_key=True, default=gen_id)
    # Nullable because a "Report a Problem" submission is about the SITE and
    # has no reported user. Both kinds share this table so staff work one
    # queue instead of two separate inboxes.
    report_type = db.Column(db.String(20), nullable=False, default="seller")  # seller|problem
    reported_user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"))
    subject = db.Column(db.String(200))
    contact_email = db.Column(db.String(255))  # so a logged-out reporter can be replied to
    reporter_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    product_id = db.Column(db.String(36), db.ForeignKey("products.id", ondelete="SET NULL"))
    reason = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    status = db.Column(db.String(20), default="open")  # open|investigating|action_taken|dismissed
    reviewed_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = db.Column(db.DateTime)
    resolution_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "report_id": self.report_id,
            "report_type": self.report_type,
            "subject": self.subject,
            "contact_email": self.contact_email,
            "reported_user_id": self.reported_user_id,
            "reporter_id": self.reporter_id,
            "product_id": self.product_id,
            "reason": self.reason,
            "details": self.details,
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": iso_utc(self.reviewed_at),
            "resolution_notes": self.resolution_notes,
            "created_at": iso_utc(self.created_at),
        }
