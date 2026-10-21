from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class PushDevice(db.Model):
    """One row per phone that can receive push notifications for a user.

    player_id is the OneSignal *subscription id* of that install. The app
    posts it to /api/profile/push-device after sign-in, so the link between
    a device and a user is made by an authenticated request - not by the
    client claiming an id with OneSignal.login(), which anyone holding the
    public App ID could do for any user.

    player_id is unique: if the same phone signs into a different account,
    the row moves to the new user instead of notifying both.
    """
    __tablename__ = "push_devices"
    id = db.Column(db.String(36), primary_key=True, default=gen_id)
    # CHAR(36) to match users.id exactly - a type mismatch is the usual cause
    # of MySQL's "foreign key constraint is incorrectly formed".
    user_id = db.Column(db.CHAR(36), db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    player_id = db.Column(db.String(64), nullable=False, unique=True)
    platform = db.Column(db.String(20), nullable=False, default="android")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "platform": self.platform,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
