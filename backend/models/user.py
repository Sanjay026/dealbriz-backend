from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.String(36), primary_key=True, default=gen_id)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), default="")
    last_name = db.Column(db.String(100), default="")
    phone = db.Column(db.String(20))
    city = db.Column(db.String(10))
    avatar_url = db.Column(db.Text)
    is_admin = db.Column(db.Boolean, default=False)
    # Suspension keeps the account and all its records intact while blocking
    # login and hiding the user's listings — preferable to deletion when a
    # report is involved, since deleting destroys the evidence.
    account_status = db.Column(db.String(20), default="active", nullable=False)  # active|suspended
    suspended_at = db.Column(db.DateTime)
    suspension_reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Account-wide "show my mobile number" setting (the app's Profile toggle).
    #   True  -> number may be shown on listings and in chat
    #   False -> number is never sent to other users, on any listing
    #   None  -> never set; each listing's own show_phone choice applies
    #            (the website's per-listing "Show phone number?" option)
    show_phone = db.Column(db.Boolean, nullable=True, default=None)
    # DealLock phase 1 strikes. No longer used (phase 3 replaced them with
    # the ban below); kept so the column stays mapped.
    seller_strikes = db.Column(db.Integer, nullable=False, default=0)
    # DealLock: confirmed deals this user cancelled in a row. At
    # rules.CANCEL_LIMIT they get deal_ban_until = now + rules.BAN_DAYS (no
    # new listings, no new deals) and the count starts again. A completed
    # deal sets it back to 0.
    consecutive_cancels = db.Column(db.Integer, nullable=False, default=0)
    deal_ban_until = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id, "email": self.email,
            "first_name": self.first_name, "last_name": self.last_name,
            "phone": self.phone, "city": self.city,
            "avatar_url": self.avatar_url, "is_admin": self.is_admin,
            "show_phone": self.show_phone,
            "account_status": self.account_status or "active",
            "suspended_at": iso_utc(self.suspended_at),
            "suspension_reason": self.suspension_reason,
            "consecutive_cancels": self.consecutive_cancels or 0,
            # Only while it is still running, so clients can just check it.
            "deal_ban_until": (iso_utc(self.deal_ban_until)
                               if self.deal_ban_until and self.deal_ban_until > datetime.utcnow()
                               else None),
            "created_at": iso_utc(self.created_at)
        }
