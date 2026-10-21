from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import DealOffer, Message, Product, User
from utils import current_user

bp = Blueprint("chat", __name__, url_prefix="/api/chat")


MAX_MESSAGE_LENGTH = 2000


@bp.route("/messages", methods=["POST"])
@jwt_required()
def send_message():
    user = current_user()
    data = request.get_json() or {}

    product_id = data.get("product_id")
    receiver_id = data.get("receiver_id")
    body = (data.get("body") or "").strip()

    if not product_id or not receiver_id or not body:
        return jsonify({"error": "Message, listing and recipient are all required"}), 400
    if len(body) > MAX_MESSAGE_LENGTH:
        return jsonify({"error": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)"}), 400
    if receiver_id == user.id:
        return jsonify({"error": "You cannot message yourself"}), 400

    product = Product.query.get(product_id)
    if not product:
        return jsonify({"error": "That listing no longer exists"}), 404

    # The recipient must actually be part of this listing's conversation —
    # either its seller, or someone who has already messaged the seller about
    # it. Without this check the endpoint accepts any user id, which turns it
    # into a way to message arbitrary users off the back of any listing.
    if receiver_id != product.posted_by:
        is_existing_thread = Message.query.filter(
            Message.product_id == product_id,
            db.or_(
                db.and_(Message.sender_id == user.id, Message.receiver_id == receiver_id),
                db.and_(Message.sender_id == receiver_id, Message.receiver_id == user.id),
            )
        ).first()
        # Only the seller may reply to a buyer who started a thread.
        if not (is_existing_thread and user.id == product.posted_by):
            return jsonify({"error": "You can only message the seller of this listing"}), 403

    if not User.query.get(receiver_id):
        return jsonify({"error": "That user no longer exists"}), 404

    msg = Message(
        product_id=product_id, sender_id=user.id,
        receiver_id=receiver_id, body=body
    )
    db.session.add(msg)
    db.session.commit()

    # After the commit, so a push is only ever sent for a message that was
    # actually saved. Runs on a background thread and never raises, so
    # OneSignal being slow or down cannot fail or delay the send.
    from push import send_chat_push
    send_chat_push(msg, sender=user, product=product)

    return jsonify(msg.to_dict()), 201


@bp.route("/conversation", methods=["GET"])
@jwt_required()
def get_conversation():
    user = current_user()
    product_id = request.args.get("product_id")
    other_id = request.args.get("other_id")
    msgs = Message.query.filter(
        Message.product_id == product_id,
        db.or_(
            db.and_(Message.sender_id == user.id, Message.receiver_id == other_id),
            db.and_(Message.sender_id == other_id, Message.receiver_id == user.id)
        )
    ).order_by(Message.created_at.asc()).all()

    # DealLock offer cards: each message that refers to an offer carries that
    # offer's CURRENT state (open, countered, accepted, expired...) and what
    # the viewer may do with it. Loaded in one query for the whole thread.
    offer_ids = {m.offer_id for m in msgs if m.offer_id}
    offers = ({o.id: o for o in DealOffer.query.filter(DealOffer.id.in_(offer_ids)).all()}
              if offer_ids else {})
    out = []
    for m in msgs:
        d = m.to_dict()
        if m.offer_id and m.offer_id in offers:
            d["offer"] = offers[m.offer_id].to_dict(viewer_id=user.id)
        out.append(d)
    return jsonify(out)


@bp.route("/conversations", methods=["GET"])
@jwt_required()
def get_conversations():
    user = current_user()
    msgs = Message.query.filter(
        db.or_(Message.sender_id == user.id, Message.receiver_id == user.id)
    ).order_by(Message.created_at.desc()).all()

    seen = {}
    for m in msgs:
        other_id = m.receiver_id if m.sender_id == user.id else m.sender_id
        key = f"{m.product_id}_{other_id}"
        if key not in seen:
            product = Product.query.get(m.product_id)
            other_user = User.query.get(other_id)
            d = m.to_dict()
            d["other_user_id"] = other_id
            d["other_user_name"] = f"{other_user.first_name} {other_user.last_name}".strip() if other_user else "User"
            d["other_user_avatar"] = other_user.avatar_url if other_user else None
            # Only when that user switched "show my number" ON in Profile.
            # Never set (None) or switched off means it isn't shared.
            d["other_user_phone"] = (
                other_user.phone if other_user and other_user.show_phone is True else None)
            d["product_title"] = product.title if product else "Listing"
            seen[key] = d
    return jsonify(list(seen.values()))


@bp.route("/mark-read", methods=["POST"])
@jwt_required()
def mark_read():
    user = current_user()
    data = request.get_json()
    Message.query.filter_by(
        product_id=data["product_id"], sender_id=data["other_id"], receiver_id=user.id
    ).update({"is_read": True})
    db.session.commit()
    return jsonify({"ok": True})
