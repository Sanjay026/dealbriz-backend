from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions import db
from models import Message, Notification
from utils import current_user

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# How many rows the dropdown asks for. The panel is a glance, not an archive.
FEED_LIMIT = 30


@bp.route("/summary", methods=["GET"])
@jwt_required()
def summary():
    """Both navbar badge counts in one call.

    This becomes the most frequently hit endpoint in the app once polling is
    on - every signed-in tab calls it on a timer. So it runs two COUNTs and
    returns nothing else: no rows, no joins, no serialisation. Returning the
    feed here as well would multiply the cost of every poll by 30 rows, for a
    number the badge renders as a single dot.
    """
    user = current_user()

    unread_notifications = db.session.query(func.count(Notification.id)).filter(
        Notification.user_id == user.id,
        Notification.is_read.is_(False),
    ).scalar() or 0

    unread_chats = db.session.query(func.count(Message.id)).filter(
        Message.receiver_id == user.id,
        Message.is_read.is_(False),
    ).scalar() or 0

    return jsonify({
        "notifications": int(unread_notifications),
        "chats": int(unread_chats),
    })


@bp.route("", methods=["GET"])
@jwt_required()
def list_notifications():
    user = current_user()
    rows = (Notification.query
            .filter(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(FEED_LIMIT)
            .all())
    return jsonify([n.to_dict() for n in rows])


@bp.route("/read", methods=["POST"])
@jwt_required()
def mark_read():
    """Marks one notification read, or all of them when no id is given.

    Both filters are scoped to the caller's own user_id, so an id belonging
    to someone else matches nothing rather than marking their row read - the
    supplied id is never trusted on its own.
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    nid = data.get("id")

    q = Notification.query.filter(
        Notification.user_id == user.id,
        Notification.is_read.is_(False),
    )
    if nid:
        q = q.filter(Notification.id == nid)

    updated = q.update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "updated": int(updated or 0)})
