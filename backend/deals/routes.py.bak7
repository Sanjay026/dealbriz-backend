"""
DealLock: offers in chat, the buyer's warning, the seller-confirmed lock,
both sides' documents, the handover, my deals, one deal with its timeline,
cancel requests.

    POST /api/deals/offers                    buyer or seller sends an offer
    POST /api/deals/offers/<id>/counter       other side counters
    POST /api/deals/offers/<id>/accept        other side accepts -> awaiting_seller
    POST /api/deals/offers/<id>/decline       other side declines
    POST /api/deals/offers/<id>/withdraw      sender takes it back
    GET  /api/deals/offers/<id>               one offer
    GET  /api/deals/thread?product_id&other_id   offers + deal for one chat
    GET  /api/deals/mine?status=live|closed   my deals
    GET  /api/deals/<id>                      one deal with its timeline
    GET  /api/deals/notices                   buyer warnings to accept, cancel
                                              requests to answer (website popups)
    POST /api/deals/<id>/acknowledge          buyer: accepts the deal warning
                                              (needed before the seller confirms)
    POST /api/deals/<id>/confirm/send-code    seller: SMS code to confirm
    POST /api/deals/<id>/confirm              seller: warning ticked + code
                                              -> docs_pending
    POST /api/deals/<id>/cancel               before the seller confirms: back
                                              out at once. After: ask the other
                                              side to cancel (24 h to answer)
    POST /api/deals/<id>/cancel/answer        other side: {agree: true|false}
    POST /api/deals/<id>/cancel/withdraw      the one who asked takes it back
    POST /api/deals/<id>/cancel/force         the one who asked ends it anyway
                                              (counts towards the pause)
    GET  /api/deals/<id>/documents            checklist with each status
    POST /api/deals/<id>/documents/loan-answer   seller: did the vehicle have a loan
    POST /api/deals/<id>/documents/<type>     buyer or seller uploads their own
                                              (multipart "file")
    GET  /api/deals/<id>/documents/<doc>/file view one (superadmin or the lender)
    POST /api/deals/<id>/documents/<doc>/review  superadmin or the deal's lender
    POST /api/deals/<id>/handover/<step>      down-payment | ready |
                                              money-received | vehicle-received |
                                              code (seller types the buyer's code)
    POST /api/deals/<id>/dispute              after Paid, problems go to staff
    GET  /api/deals/rules                     numbers the UI needs

Both sides read a deal warning before the deal is locked: first the buyer
(POST /<id>/acknowledge, after an offer is accepted), then the seller
(POST /<id>/confirm needs "acknowledged": true). Making, countering and
accepting offers needs no warning.

  Staff (lender-side steps done by hand for lenders without portal users):
    GET  /api/deals/staff/queue               deals needing attention
    POST /api/deals/staff/<id>/financer       set the deal's lender
    POST /api/deals/staff/<id>/ready-to-pay   loan sanctioned, meeting can be booked
    POST /api/deals/staff/<id>/paid           lender paid the seller (UTR)
    POST /api/deals/staff/<id>/resolve        close a dispute

The loan itself (apply, status) is in loans/routes.py, the lender's own
portal in lender/routes.py. Lender pre-check and webhooks wait for a
lender's API.

Every change locks the rows it reads (SELECT ... FOR UPDATE), and the
database's unique indexes are the final word on "one open offer per buyer"
and "one live deal per listing", so two taps at the same moment can't both
win. When the database refuses, the request gets a 409 asking to refresh.
"""
import logging
from datetime import timedelta

from flask import Blueprint, jsonify, redirect, request, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Deal, DealDocument, DealEvent, DealOffer, Financer, Product, User
from models.deal import (CONFIRMED_STATES, DEAL_AWAITING_SELLER, DEAL_DISPUTED,
                         DEAL_DOCS_PENDING,
                         DEAL_LOCKED, DEAL_PAID, DEAL_READY_TO_PAY,
                         DOC_APPROVED, DOC_REJECTED, DOC_UPLOADED,
                         LIVE_DEAL_STATES, LOAN_VEHICLE, OFFER_ACCEPTED,
                         OFFER_COUNTERED, OFFER_DECLINED, OFFER_EXPIRED,
                         OFFER_OPEN, OFFER_SUPERSEDED, OFFER_WITHDRAWN,
                         RELEASABLE_STATES, RELEASE_BUYER_WITHDREW, RELEASE_SELLER_CANCELLED,
                         RELEASE_SELLER_DECLINED)
from utils import is_superadmin
from permissions import has_capability, require_capability
from utils import current_user, has_staff_access
from . import doc_storage, listing_docs, otp, rules
from .service import (Outbox, add_event, answer_cancel, can_review_docs, can_view_deal,
                      clean_utr, complete_deal, confirm_deal, display_name,
                      docs_all_approved, docs_checklist, docs_question_pending,
                      force_cancel, party_docs_uploaded, request_cancel, withdraw_cancel,
                      financer_name, financer_supports, has_chat_thread, inr,
                      lender_financer_id, listing_blocked_reason,
                      live_deal_for_product, loan_summary, lock_deal,
                      mark_paid, mark_ready_to_pay, new_handover_code, notify,
                      notify_doc_reviewers, notify_staff, own_block_reason,
                      party_block_reason, post_thread_message, release_deal,
                      short_date, utcnow, PRODUCT_RESERVED)

log = logging.getLogger("dealbridge.deals")

bp = Blueprint("deals", __name__, url_prefix="/api/deals")

RACE_MESSAGE = "Something changed while you were doing that. Please refresh and try again."
# A live deal holds the listing from the moment an offer is accepted, even
# before it is marked Reserved (that happens when the deal locks).
IN_A_DEAL = "Another buyer is already in a deal for this listing."


def _err(message, code):
    return jsonify({"error": message}), code


def _me():
    user = current_user()
    if not user:
        return None
    if (user.account_status or "active") != "active":
        return None
    return user


def _body():
    return request.get_json(silent=True) or {}


def _person(user):
    if not user:
        return None
    return {"id": user.id, "name": display_name(user), "avatar_url": user.avatar_url}


def _product_summary(product):
    if not product:
        return None
    return {"id": product.id, "title": product.title, "category": product.category,
            "price": float(product.price), "image_url": product.image_url,
            "status": product.status, "city": product.city, "location": product.location}


def _commit_or_conflict():
    """Commit; a unique-index refusal means another request won the race."""
    try:
        db.session.commit()
        return None
    except IntegrityError:
        db.session.rollback()
        log.info("DealLock race refused by a unique index", exc_info=True)
        return _err(RACE_MESSAGE, 409)


def _load_offer_for_update(offer_id):
    return DealOffer.query.filter_by(id=offer_id).with_for_update().first()


def _expire_if_lapsed(offer):
    """An open offer past its deadline is expired now, whatever the timer job
    has or hasn't done yet. Returns True if it had lapsed."""
    if offer.status == OFFER_OPEN and not offer.is_live():
        offer.status = OFFER_EXPIRED
        offer.responded_at = utcnow()
        db.session.commit()
        return True
    return False


def _confirm_words():
    """How the seller confirms, for message text."""
    return "your account password" if otp.confirm_method() == "password" else "the SMS code"


def _acknowledged(data):
    return data.get("acknowledged") is True


ACK_MESSAGE = "Please read the deal warning and tick \"I understand\" first."


def _offer_message_text(kind_label, price, note):
    text = f"{kind_label}: {inr(price)}. Valid for {rules.OFFER_HOURS} hours."
    if note:
        text += f"\n{note}"
    return text


def _new_offer(product, buyer_id, from_user, price, note, round_number, previous, acknowledged=False):
    now = utcnow()
    offer = DealOffer(
        product_id=product.id, buyer_id=buyer_id, seller_id=product.posted_by,
        from_user_id=from_user.id, previous_offer_id=previous.id if previous else None,
        price=price, round_number=round_number, status=OFFER_OPEN,
        note=note or None, expires_at=now + timedelta(hours=rules.OFFER_HOURS),
        acknowledged_at=now if acknowledged else None,
    )
    db.session.add(offer)
    db.session.flush()   # needs offer.id for the message
    return offer


def _announce_offer(offer, product, from_user, label, outbox):
    to_id = offer.buyer_id if from_user.id == offer.seller_id else offer.seller_id
    text = _offer_message_text(label, offer.price, offer.note)
    post_thread_message(product.id, from_user.id, to_id, text, "offer", offer.id)
    title = f"{label} from {display_name(from_user)}"
    notify(to_id, title[:200], f"{inr(offer.price)} for {product.title}", product.id)
    outbox.push(to_id, f"{display_name(from_user)} · {product.title}",
                f"{label}: {inr(offer.price)}", product.id, from_user.id)


# ==========================================================================
# Rules
# ==========================================================================

@bp.route("/rules", methods=["GET"])
def get_rules():
    out = rules.public_rules()
    # How sellers confirm a deal right now: "sms" (code) or "password".
    out["confirm_method"] = otp.confirm_method()
    return jsonify(out)


# ==========================================================================
# Offers
# ==========================================================================

@bp.route("/offers", methods=["POST"])
@jwt_required()
def send_offer():
    """A new offer in a chat about a listing, from either side.

    Seller: {product_id, buyer_id, price, note?}. The buyer must already have
    messaged about the listing (offers never go to an arbitrary user id).
    Buyer:  {product_id, price, note?}. The offer itself starts the chat if
    there wasn't one.

    One live offer per buyer per listing (design). If the other side's offer
    is still open, this is refused: answer that one (accept, counter or
    decline). The sender's own open offer is replaced, e.g. after the
    vehicle inspection, and a fresh chain of counters starts."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    data = _body()

    product = Product.query.filter_by(id=data.get("product_id")).with_for_update().first()
    if not product:
        return _err("That listing no longer exists.", 404)
    reason = listing_blocked_reason(product)
    if reason:
        return _err(reason, 409)
    reason = own_block_reason(user)
    if reason:
        return _err(reason, 403)

    if product.posted_by == user.id:
        buyer = User.query.get(data.get("buyer_id")) if data.get("buyer_id") else None
        if not buyer or buyer.id == user.id:
            return _err("Choose the buyer you are making the offer to.", 400)
        if not has_chat_thread(product.id, user.id, buyer.id):
            return _err("You can send an offer to someone who has messaged you about this listing.", 403)
        reason = party_block_reason(buyer, "buyer")
        if reason:
            return _err(reason, 409)
    else:
        buyer = user
        seller = User.query.get(product.posted_by)
        reason = party_block_reason(seller, "seller")
        if reason:
            return _err(reason, 409)

    price, problem = rules.parse_price(data.get("price"))
    if problem:
        return _err(problem, 400)
    note = (data.get("note") or "").strip()
    if len(note) > rules.MAX_NOTE_CHARS:
        return _err(f"Please keep the note under {rules.MAX_NOTE_CHARS} characters.", 400)

    if live_deal_for_product(product.id, lock=True):
        return _err(IN_A_DEAL, 409)

    now = utcnow()
    open_offers = (DealOffer.query
                   .filter_by(product_id=product.id, buyer_id=buyer.id, status=OFFER_OPEN)
                   .with_for_update().all())
    for old in open_offers:
        if old.from_user_id != user.id and old.expires_at > now:
            who = "seller" if old.from_user_id == product.posted_by else "buyer"
            return _err(f"The {who}'s offer of {inr(old.price)} is waiting for your answer. "
                        "Accept it, counter it or decline it first.", 409)
    for old in open_offers:
        old.status = OFFER_SUPERSEDED if old.expires_at > now else OFFER_EXPIRED
        old.responded_at = now
    db.session.flush()   # free the "one open offer" slot before inserting

    outbox = Outbox()
    offer = _new_offer(product, buyer.id, user, price, note, 1, None)
    _announce_offer(offer, product, user, "Offer", outbox)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(offer.to_dict(viewer_id=user.id)), 201


@bp.route("/offers/<offer_id>", methods=["GET"])
@jwt_required()
def get_offer(offer_id):
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    offer = DealOffer.query.get(offer_id)
    if not offer:
        return _err("Offer not found.", 404)
    if user.id not in (offer.buyer_id, offer.seller_id) and not has_staff_access(user):
        return _err("Offer not found.", 404)
    return jsonify(offer.to_dict(viewer_id=user.id))


@bp.route("/offers/<offer_id>/counter", methods=["POST"])
@jwt_required()
def counter_offer(offer_id):
    """{price, note?}"""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    data = _body()

    offer = _load_offer_for_update(offer_id)
    if not offer or user.id not in (offer.buyer_id, offer.seller_id):
        return _err("Offer not found.", 404)
    if user.id == offer.from_user_id:
        return _err("You can't counter your own offer. Wait for the other side to reply.", 409)
    if _expire_if_lapsed(offer):
        return _err("This offer has expired.", 409)
    if offer.status != OFFER_OPEN:
        return _err(f"This offer is already {offer.status}.", 409)
    is_buyer = user.id == offer.buyer_id

    counters_so_far = offer.round_number - 1
    if counters_so_far >= rules.MAX_COUNTERS:
        return _err(f"The {rules.MAX_COUNTERS} counter-offers are used up. Accept or decline "
                    "this offer, or send a fresh offer after declining.", 409)

    product = Product.query.filter_by(id=offer.product_id).with_for_update().first()
    reason = listing_blocked_reason(product)
    if reason:
        return _err(reason, 409)
    reason = own_block_reason(user)
    if reason:
        return _err(reason, 403)
    other = User.query.get(offer.seller_id if is_buyer else offer.buyer_id)
    reason = party_block_reason(other, "seller" if is_buyer else "buyer")
    if reason:
        return _err(reason, 409)
    if live_deal_for_product(product.id, lock=True):
        return _err(IN_A_DEAL, 409)

    price, problem = rules.parse_price(data.get("price"))
    if problem:
        return _err(problem, 400)
    note = (data.get("note") or "").strip()
    if len(note) > rules.MAX_NOTE_CHARS:
        return _err(f"Please keep the note under {rules.MAX_NOTE_CHARS} characters.", 400)

    offer.status = OFFER_COUNTERED
    offer.responded_at = utcnow()
    db.session.flush()

    outbox = Outbox()
    new = _new_offer(product, offer.buyer_id, user, price, note,
                     offer.round_number + 1, offer)
    _announce_offer(new, product, user, "Counter-offer", outbox)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(new.to_dict(viewer_id=user.id)), 201


@bp.route("/offers/<offer_id>/accept", methods=["POST"])
@jwt_required()
def accept_offer(offer_id):
    """Agrees the price. The listing is held at once, and the seller then
    has CONFIRM_HOURS to confirm (warning + SMS code): the seller always
    takes the final decision to lock. If the seller is the one accepting,
    the app goes straight on to that confirm step. The buyer sees no
    warning here: the warning belongs to the seller's confirmation, and the
    buyer is told (popup, POST /<id>/acknowledge) once the seller confirms."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)

    offer = _load_offer_for_update(offer_id)
    if not offer or user.id not in (offer.buyer_id, offer.seller_id):
        return _err("Offer not found.", 404)
    if user.id == offer.from_user_id:
        return _err("You can't accept your own offer.", 409)
    if _expire_if_lapsed(offer):
        return _err("This offer has expired.", 409)
    if offer.status != OFFER_OPEN:
        return _err(f"This offer is already {offer.status}.", 409)
    is_buyer = user.id == offer.buyer_id

    product = Product.query.filter_by(id=offer.product_id).with_for_update().first()
    reason = listing_blocked_reason(product)
    if reason:
        return _err(reason, 409)
    if product.posted_by != offer.seller_id:
        return _err("This listing has changed hands. The offer can't be accepted.", 409)

    reason = own_block_reason(user)
    if reason:
        return _err(reason, 403)
    seller = User.query.get(offer.seller_id)
    buyer = User.query.get(offer.buyer_id)
    reason = (party_block_reason(seller, "seller") if is_buyer
              else party_block_reason(buyer, "buyer"))
    if reason:
        return _err(reason, 409)
    if live_deal_for_product(product.id, lock=True):
        return _err(IN_A_DEAL, 409)

    now = utcnow()
    offer.status = OFFER_ACCEPTED
    offer.responded_at = now
    confirm_by = now + timedelta(hours=rules.CONFIRM_HOURS)
    deal = Deal(
        accepted_offer_id=offer.id, product_id=product.id,
        buyer_id=offer.buyer_id, seller_id=offer.seller_id,
        agreed_price=offer.price, status=DEAL_AWAITING_SELLER, locked_at=now,
        # Only a deadline for the next step here. The listing is held (no
        # other buyer can start a deal) but not marked Reserved: that and
        # the RESERVATION_DAYS reservation start when the deal locks.
        reserved_until=confirm_by, confirm_by=confirm_by,
        loan_type=rules.loan_type_for_category(product.category),
        # Set when the buyer accepts the deal warning. The seller can't
        # confirm before that.
        buyer_ack_at=None,
    )
    db.session.add(deal)
    db.session.flush()

    add_event(deal, "accepted", user.id, price=float(offer.price), offer_id=offer.id,
              loan_type=deal.loan_type, confirm_by=confirm_by.isoformat() + "Z")

    other_id = offer.from_user_id
    who = "buyer" if is_buyer else "seller"
    text = (f"Offer of {inr(offer.price)} accepted by the {who}. Next, the buyer reads and "
            f"accepts the deal warning, then the seller confirms with {_confirm_words()}. Each has "
            f"{rules.CONFIRM_HOURS} hours (first deadline {short_date(confirm_by)}).")
    post_thread_message(product.id, user.id, other_id, text, "system", offer.id)

    outbox = Outbox()
    if is_buyer:
        title = "Offer accepted by the buyer"
    else:
        title = "Accept the deal warning"
    notify(other_id, f"{title} \u00b7 {product.title}"[:200], text, product.id)
    outbox.push(other_id, f"{title} \u00b7 {product.title}", text, product.id, user.id)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict(viewer_id=user.id)), 201


def _close_offer(offer_id, new_status):
    """decline (other side) and withdraw (sender) share everything but who
    may do it and the wording."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)

    offer = _load_offer_for_update(offer_id)
    if not offer or user.id not in (offer.buyer_id, offer.seller_id):
        return _err("Offer not found.", 404)
    if new_status == OFFER_DECLINED and user.id == offer.from_user_id:
        return _err("You can't decline your own offer. Withdraw it instead.", 409)
    if new_status == OFFER_WITHDRAWN and user.id != offer.from_user_id:
        return _err("Only the person who sent this offer can withdraw it.", 409)
    if _expire_if_lapsed(offer):
        return _err("This offer has expired.", 409)
    if offer.status != OFFER_OPEN:
        return _err(f"This offer is already {offer.status}.", 409)

    offer.status = new_status
    offer.responded_at = utcnow()

    product = Product.query.get(offer.product_id)
    title = product.title if product else "the listing"
    other_id = offer.buyer_id if user.id == offer.seller_id else offer.seller_id
    verb = "declined" if new_status == OFFER_DECLINED else "withdrawn"
    text = f"Offer of {inr(offer.price)} {verb}."
    post_thread_message(offer.product_id, user.id, other_id, text, "system", offer.id)

    outbox = Outbox()
    notify(other_id, f"Offer {verb} · {title}"[:200], text, offer.product_id)
    outbox.push(other_id, f"{display_name(user)} · {title}", text, offer.product_id, user.id)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(offer.to_dict(viewer_id=user.id))


@bp.route("/offers/<offer_id>/decline", methods=["POST"])
@jwt_required()
def decline_offer(offer_id):
    return _close_offer(offer_id, OFFER_DECLINED)


@bp.route("/offers/<offer_id>/withdraw", methods=["POST"])
@jwt_required()
def withdraw_offer(offer_id):
    return _close_offer(offer_id, OFFER_WITHDRAWN)


# ==========================================================================
# One chat thread: everything a chat screen needs to draw the deal state
# ==========================================================================

@bp.route("/thread", methods=["GET"])
@jwt_required()
def get_thread():
    """Offers and the deal between the signed-in user and `other_id` about
    `product_id`, plus whether the user may send a new offer here and, if
    not, why (so the button can say so instead of failing on tap)."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = Product.query.get(request.args.get("product_id"))
    other_id = request.args.get("other_id")
    if not product or not other_id:
        return _err("Listing and other user are both required.", 400)

    if user.id == product.posted_by:
        buyer_id, seller_id = other_id, user.id
    elif other_id == product.posted_by:
        buyer_id, seller_id = user.id, other_id
    else:
        return _err("Not found.", 404)

    offers = (DealOffer.query
              .filter_by(product_id=product.id, buyer_id=buyer_id)
              .order_by(DealOffer.created_at.desc())
              .limit(20).all())
    current = next((o for o in offers if o.status == OFFER_OPEN and o.is_live()), None)

    deal = (Deal.query
            .filter_by(product_id=product.id, buyer_id=buyer_id)
            .order_by(Deal.created_at.desc())
            .first())

    is_seller = user.id == seller_id
    other = User.query.get(other_id)
    why_not = (listing_blocked_reason(product) or own_block_reason(user)
               or party_block_reason(other, "buyer" if is_seller else "seller"))
    live = live_deal_for_product(product.id)
    if not why_not and live is not None and live.buyer_id != buyer_id:
        why_not = IN_A_DEAL
    if not why_not and current is not None and current.from_user_id != user.id:
        why_not = "Answer the offer above first: accept, counter or decline it."
    if not why_not and is_seller and not has_chat_thread(product.id, seller_id, buyer_id):
        why_not = "The buyer needs to message you first."
    can_send = why_not is None

    return jsonify({
        "product": _product_summary(product),
        "my_role": "seller" if is_seller else "buyer",
        "loan_type": rules.loan_type_for_category(product.category),
        "current_offer": current.to_dict(viewer_id=user.id) if current else None,
        "offers": [o.to_dict(viewer_id=user.id) for o in offers],
        "deal": deal.to_dict(viewer_id=user.id) if deal else None,
        "loan": loan_summary(deal, user.id) if deal else None,
        "listing_reserved_for_someone_else": bool(live is not None and live.buyer_id != buyer_id),
        "can_send_offer": can_send,
        "cannot_send_offer_reason": why_not,
        "my_deal_ban_until": user.to_dict()["deal_ban_until"],
        "rules": rules.public_rules(),
    })


# ==========================================================================
# Deals
# ==========================================================================

@bp.route("/mine", methods=["GET"])
@jwt_required()
def my_deals():
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    q = Deal.query.filter(db.or_(Deal.buyer_id == user.id, Deal.seller_id == user.id))
    which = (request.args.get("status") or "all").lower()
    if which == "live":
        q = q.filter(Deal.status.in_(LIVE_DEAL_STATES))
    elif which == "closed":
        q = q.filter(~Deal.status.in_(LIVE_DEAL_STATES))
    deals = q.order_by(Deal.created_at.desc()).limit(100).all()

    product_ids = {d.product_id for d in deals}
    user_ids = {d.buyer_id for d in deals} | {d.seller_id for d in deals}
    products = {p.id: p for p in Product.query.filter(Product.id.in_(product_ids)).all()} if product_ids else {}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    out = []
    for d in deals:
        row = d.to_dict(viewer_id=user.id)
        other = d.seller_id if d.buyer_id == user.id else d.buyer_id
        row["product"] = _product_summary(products.get(d.product_id))
        row["other_user"] = _person(users.get(other))
        out.append(row)
    return jsonify(out)


def deal_detail(deal, user):
    """One deal as the viewer may see it: timeline, loan, documents and
    what this viewer may do next. Shared with the lender portal."""
    events = (DealEvent.query.filter_by(deal_id=deal.id)
              .order_by(DealEvent.created_at.asc()).all())
    is_party = user.id in (deal.buyer_id, deal.seller_id)
    other = deal.seller_id if deal.buyer_id == user.id else deal.buyer_id
    d = deal.to_dict(viewer_id=user.id)
    d["product"] = _product_summary(Product.query.get(deal.product_id))
    if is_party:
        d["other_user"] = _person(User.query.get(other))
    else:
        d["buyer"] = _person(User.query.get(deal.buyer_id))
        d["seller"] = _person(User.query.get(deal.seller_id))
    offer = DealOffer.query.get(deal.accepted_offer_id)
    d["accepted_offer"] = offer.to_dict(viewer_id=user.id) if offer else None
    d["events"] = [e.to_dict() for e in events]
    staff_view = (not is_party) and can_view_deal(user, deal)
    d["loan"] = loan_summary(deal, user.id, is_staff=staff_view or has_staff_access(user))
    d["lender"] = financer_name(deal.financer_id)
    product = Product.query.get(deal.product_id)
    d["documents"] = docs_checklist(deal, product)
    d["docs_question_pending"] = docs_question_pending(deal, product)
    # Phase 5: the seller's documents are on the listing (checked once);
    # in the deal only the buyer uploads.
    d["listing_docs"] = listing_docs.summary(product) if product is not None else None
    if product is not None:
        d["vehicle_had_loan"] = product.vehicle_had_loan
    d["confirm_method"] = otp.confirm_method()
    d["can_upload_docs"] = user.id == deal.buyer_id and deal.status == DEAL_DOCS_PENDING
    d["docs_uploaded"] = {"buyer": party_docs_uploaded(deal, "buyer"),
                          "seller": party_docs_uploaded(deal, "seller")}
    # Opening and verifying files: superadmin or the deal's lender only.
    d["can_review_docs"] = deal.status == DEAL_DOCS_PENDING and can_review_docs(user, deal)
    d["can_view_doc_files"] = can_review_docs(user, deal)
    d["can_enter_handover_code"] = user.id == deal.seller_id and deal.status == DEAL_PAID
    return d


@bp.route("/<deal_id>", methods=["GET"])
@jwt_required()
def get_deal(deal_id):
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = Deal.query.get(deal_id)
    if not deal or not can_view_deal(user, deal):
        return _err("Deal not found.", 404)
    return jsonify(deal_detail(deal, user))


# ==========================================================================
# Seller confirmation: the final decision to lock (warning + SMS code)
# ==========================================================================

def _seller_deal_for_confirm(deal_id, user):
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return None, _err("Deal not found.", 404)
    if user.id != deal.seller_id:
        return None, _err("Only the seller confirms the deal.", 403)
    if deal.status != DEAL_AWAITING_SELLER:
        return None, _err(f"This deal is {deal.status.replace('_', ' ')}; there is nothing to confirm.", 409)
    if deal.confirm_by and deal.confirm_by <= utcnow():
        return None, _err("The time to confirm this deal has run out.", 409)
    if deal.buyer_ack_at is None:
        return None, _err("Waiting for the buyer to read and accept the deal warning. "
                          "You can confirm once they have.", 409)
    reason = own_block_reason(user)
    if reason:
        return None, _err(reason, 403)
    reason = party_block_reason(User.query.get(deal.buyer_id), "buyer")
    if reason:
        return None, _err(reason, 409)
    return deal, None


@bp.route("/<deal_id>/confirm/send-code", methods=["POST"])
@jwt_required()
def confirm_send_code(deal_id):
    """Texts the seller a 6-digit code for confirming this deal. In password
    mode (no SMS provider set up) there is nothing to send: answers
    {"method": "password"}."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal, problem = _seller_deal_for_confirm(deal_id, user)
    if problem:
        return problem
    if otp.confirm_method() == "password":
        return jsonify({"method": "password"})
    try:
        info = otp.send_code(user, otp.PURPOSE_DEAL_CONFIRM, deal.id, utcnow())
    except otp.OtpError as exc:
        db.session.rollback()
        return _err(exc.message, exc.code)
    db.session.commit()
    return jsonify(info)


@bp.route("/<deal_id>/confirm", methods=["POST"])
@jwt_required()
def confirm(deal_id):
    """{acknowledged: true, code: "123456"} (SMS method) or
    {acknowledged: true, password: "..."} (password method, when no SMS
    provider is set up). Only after the buyer accepted the warning. The
    deal then waits for the buyer's documents and the listing's
    (docs_pending) and locks when they are all verified."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    data = _body()
    if not _acknowledged(data):
        return _err(ACK_MESSAGE, 400)
    deal, problem = _seller_deal_for_confirm(deal_id, user)
    if problem:
        return problem
    try:
        if otp.confirm_method() == "password":
            otp.check_password(user, deal.id, data.get("password"), utcnow())
        else:
            otp.check_code(user, otp.PURPOSE_DEAL_CONFIRM, deal.id, data.get("code"), utcnow())
    except otp.OtpError as exc:
        db.session.commit()   # keep the wrong-try count
        return _err(exc.message, exc.code)

    outbox = Outbox()
    confirm_deal(deal, user, outbox)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal_detail(deal, user))


# ==========================================================================
# Things waiting for the viewer: the deal warning (buyer), cancel requests
# ==========================================================================

@bp.route("/notices", methods=["GET"])
@jwt_required()
def notices():
    """What the website pops up for the viewer, polled with the nav badges:
      warnings         deals where the viewer, as buyer, must accept the deal
                       warning before the seller can lock
      cancel_requests  cancel requests from the other side, waiting for the
                       viewer to agree or disagree
    Two small indexed queries; both lists are empty for almost everyone."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    now = utcnow()
    warn = (Deal.query
            .filter(Deal.buyer_id == user.id, Deal.status == DEAL_AWAITING_SELLER,
                    Deal.buyer_ack_at.is_(None), Deal.confirm_by > now)
            .order_by(Deal.created_at.asc()).limit(10).all())
    cancel = (Deal.query
              .filter(db.or_(Deal.buyer_id == user.id, Deal.seller_id == user.id),
                      Deal.cancel_requested_by.isnot(None),
                      Deal.cancel_requested_by != user.id,
                      Deal.cancel_request_expires_at > now,
                      Deal.status.in_(CONFIRMED_STATES))
              .order_by(Deal.cancel_requested_at.asc()).limit(10).all())

    def row(d):
        r = d.to_dict(viewer_id=user.id)
        r["product"] = _product_summary(Product.query.get(d.product_id))
        other = d.seller_id if d.buyer_id == user.id else d.buyer_id
        r["other_user"] = _person(User.query.get(other))
        return r
    return jsonify({"warnings": [row(d) for d in warn],
                    "cancel_requests": [row(d) for d in cancel]})


@bp.route("/<deal_id>/acknowledge", methods=["POST"])
@jwt_required()
def buyer_acknowledge(deal_id):
    """{acknowledged: true}: the buyer read and ticked the deal warning.
    Needed before the seller can confirm. The seller then gets a fresh
    CONFIRM_HOURS to confirm. Idempotent (two tabs)."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return _err("Deal not found.", 404)
    if user.id != deal.buyer_id:
        return _err("Only the buyer accepts the buyer's deal warning.", 403)
    if deal.buyer_ack_at is not None:
        return jsonify(deal_detail(deal, user))
    if deal.status != DEAL_AWAITING_SELLER:
        return _err(f"This deal is {deal.status.replace('_', ' ')}.", 409)
    if deal.confirm_by and deal.confirm_by <= utcnow():
        return _err("The time to accept this deal has run out.", 409)
    if not _acknowledged(_body()):
        return _err(ACK_MESSAGE, 400)

    now = utcnow()
    deal.buyer_ack_at = now
    deal.confirm_by = now + timedelta(hours=rules.CONFIRM_HOURS)
    deal.reserved_until = deal.confirm_by
    add_event(deal, "buyer_acknowledged", user.id)
    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    text = (f"The buyer read and accepted the deal warning. Seller: confirm with {_confirm_words()} "
            f"by {short_date(deal.confirm_by)}, or decline.")
    post_thread_message(deal.product_id, user.id, deal.seller_id, text, "system")
    outbox = Outbox()
    notify(deal.seller_id, f"Confirm the deal \u00b7 {title}"[:200], text, deal.product_id)
    outbox.push(deal.seller_id, f"Confirm the deal \u00b7 {title}", text, deal.product_id, user.id)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal_detail(deal, user))


# ==========================================================================
# Cancel
# ==========================================================================

def _cancel_note():
    note = (_body().get("reason") or "").strip()
    if len(note) > rules.MAX_CANCEL_NOTE_CHARS:
        return None, _err(f"Please keep the reason under {rules.MAX_CANCEL_NOTE_CHARS} characters.", 400)
    return note, None


def _party_deal(deal_id, user):
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return None
    return deal


def _ban_info(user_id, counted=None):
    me = User.query.get(user_id)
    return {"counted": counted is not None,
            "consecutive_cancels": me.consecutive_cancels or 0,
            "cancel_limit": rules.CANCEL_LIMIT,
            "deal_ban_until": me.to_dict()["deal_ban_until"]}


@bp.route("/<deal_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_deal(deal_id):
    """{reason?}
    Before the seller confirms (awaiting_seller): either side backs out at
    once, never counted. The seller's "no" is seller_declined.
    After (documents pending through Ready to pay): this ASKS the other side
    to cancel. They agree (POST .../cancel/answer) or not, within
    CANCEL_REQUEST_HOURS. Answers 202 with the deal and its cancel_request."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _party_deal(deal_id, user)
    if not deal:
        return _err("Deal not found.", 404)
    note, problem = _cancel_note()
    if problem:
        return problem

    outbox = Outbox()
    if deal.status == DEAL_AWAITING_SELLER:
        reason = RELEASE_BUYER_WITHDREW if user.id == deal.buyer_id else RELEASE_SELLER_DECLINED
        release_deal(deal, reason, user.id, outbox, note=note)
        code = 200
    elif deal.status in CONFIRMED_STATES:
        problem = request_cancel(deal, user, note, outbox)
        if problem:
            db.session.rollback()
            return _err(problem, 409)
        code = 202
    elif deal.is_live:
        return _err("The seller has already been paid, so this deal can't be cancelled. "
                    "Please report a problem instead.", 409)
    else:
        return _err(f"This deal is already {deal.status}.", 409)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    d = deal_detail(deal, user)
    d.update(_ban_info(user.id))
    return jsonify(d), code


@bp.route("/<deal_id>/cancel/answer", methods=["POST"])
@jwt_required()
def cancel_answer(deal_id):
    """{agree: true|false}. The other side answers a cancel request. Agree:
    the deal is cancelled and nobody is penalised."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _party_deal(deal_id, user)
    if not deal:
        return _err("Deal not found.", 404)
    agree = _body().get("agree")
    if not isinstance(agree, bool):
        return _err("Answer agree or disagree.", 400)
    outbox = Outbox()
    problem = answer_cancel(deal, user, agree, outbox)
    if problem:
        db.session.rollback()
        return _err(problem, 409)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal_detail(deal, user))


@bp.route("/<deal_id>/cancel/withdraw", methods=["POST"])
@jwt_required()
def cancel_withdraw(deal_id):
    """The one who asked takes the cancel request back."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _party_deal(deal_id, user)
    if not deal:
        return _err("Deal not found.", 404)
    outbox = Outbox()
    problem = withdraw_cancel(deal, user, outbox)
    if problem:
        db.session.rollback()
        return _err(problem, 409)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal_detail(deal, user))


@bp.route("/<deal_id>/cancel/force", methods=["POST"])
@jwt_required()
def cancel_force(deal_id):
    """{acknowledged: true}: the one who asked to cancel ends the deal
    without the other side's agreement, after reading the warning. Counts:
    rules.CANCEL_LIMIT force cancels in a row pause new listings and deals
    for rules.BAN_DAYS days."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _party_deal(deal_id, user)
    if not deal:
        return _err("Deal not found.", 404)
    if not _acknowledged(_body()):
        return _err("Please read the force cancel warning and tick \"I understand\" first.", 400)
    outbox = Outbox()
    problem, counted = force_cancel(deal, user, outbox)
    if problem:
        db.session.rollback()
        return _err(problem, 409)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    d = deal_detail(deal, user)
    d.update(_ban_info(user.id, counted))
    return jsonify(d)


# ==========================================================================
# Documents: the seller's (vehicle loans) and the buyer's (every deal).
# Upload by their own side, open and verify by superadmin or the lender.
# ==========================================================================

def _doc_deal(deal_id, user, lock=False):
    q = Deal.query.filter_by(id=deal_id)
    if lock:
        q = q.with_for_update()
    deal = q.first()
    if not deal or not can_view_deal(user, deal):
        return None
    return deal


@bp.route("/<deal_id>/documents", methods=["GET"])
@jwt_required()
def list_documents(deal_id):
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _doc_deal(deal_id, user)
    if not deal:
        return _err("Deal not found.", 404)
    return jsonify({"status": deal.status, "loan_type": deal.loan_type,
                    "vehicle_had_loan": deal.vehicle_had_loan,
                    "question_pending": docs_question_pending(deal),
                    "documents": docs_checklist(deal)})


@bp.route("/<deal_id>/documents/loan-answer", methods=["POST"])
@jwt_required()
def documents_loan_answer(deal_id):
    """{had_loan: true|false}: was there ever a loan on this vehicle (a
    bank's name on the RC)? Yes adds the old lender's NOC to the list."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _doc_deal(deal_id, user, lock=True)
    if not deal or user.id != deal.seller_id:
        return _err("Deal not found.", 404)
    if deal.status != DEAL_DOCS_PENDING or deal.loan_type != LOAN_VEHICLE:
        return _err("This can only be answered while the documents are pending.", 409)
    had = _body().get("had_loan")
    if not isinstance(had, bool):
        return _err("Answer yes or no.", 400)
    # Phase 5: the answer belongs to the listing (it decides the listing's
    # documents); the deal keeps a copy for its own record.
    product = Product.query.filter_by(id=deal.product_id).with_for_update().first()
    outbox = Outbox()
    deal.vehicle_had_loan = had
    if product is not None and product.vehicle_had_loan != had:
        before = listing_docs.state(product)
        product.vehicle_had_loan = had
        db.session.flush()
        add_event(deal, "loan_answer", user.id, had_loan=had)
        listing_docs.after_change(product, before, user.id, outbox)
    elif docs_all_approved(deal):
        lock_deal(deal, user.id, outbox)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal_detail(deal, user))


@bp.route("/<deal_id>/documents/<doc_type>", methods=["POST"])
@jwt_required()
def upload_document(deal_id, doc_type):
    """Multipart form with "file" (JPG, PNG, WEBP or PDF, up to 8 MB).
    Uploading again replaces the earlier file of that type, unless it was
    already approved."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = _doc_deal(deal_id, user, lock=True)
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return _err("Deal not found.", 404)
    if doc_type not in rules.DOC_TYPES:
        return _err("Unknown document type.", 404)
    party = "buyer" if user.id == deal.buyer_id else "seller"
    if rules.DOC_PARTY[doc_type] != party:
        return _err(f"That document is the {rules.DOC_PARTY[doc_type]}'s to upload.", 403)
    if doc_type in rules.SELLER_DOC_TYPES:
        return _err("Your documents are uploaded on the listing now, not in the deal: open "
                    "My Listings and tap Edit listing.", 409)
    if deal.status != DEAL_DOCS_PENDING:
        return _err("Documents can only be uploaded while the deal is waiting for them.", 409)
    if doc_type not in rules.required_buyer_docs():
        return _err("That document isn't needed for this deal.", 409)

    file = request.files.get("file")
    ext, content_type, size, problem = doc_storage.check_upload(file, rules.DOC_EXTENSIONS, rules.DOC_MAX_BYTES)
    if problem:
        return _err(problem, 400)

    doc = DealDocument.query.filter_by(deal_id=deal.id, doc_type=doc_type).with_for_update().first()
    if doc is not None and doc.status == DOC_APPROVED:
        return _err("This document is already approved.", 409)

    try:
        key = doc_storage.save(file, deal.id, doc_type, ext, content_type)
    except Exception:
        log.error("Deal document upload failed", exc_info=True)
        db.session.rollback()
        return _err("Could not save that file. Please try again.", 500)

    old_key = doc.storage_key if doc is not None else None
    now = utcnow()
    safe_name = "".join(ch for ch in file.filename if ch.isalnum() or ch in "._- ")[:200] or f"{doc_type}.{ext}"
    if doc is None:
        doc = DealDocument(deal_id=deal.id, doc_type=doc_type)
        db.session.add(doc)
    doc.status = DOC_UPLOADED
    doc.storage_key = key
    doc.file_name = safe_name
    doc.content_type = content_type
    doc.size_bytes = size
    doc.uploaded_by = user.id
    doc.uploaded_at = now
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.reject_reason = None
    doc.deleted_at = None
    add_event(deal, "document_uploaded", user.id, doc_type=doc_type)

    # Tell reviewers once one side has everything in, not on every file,
    # and tell the other side (who may be waiting on it).
    db.session.flush()
    if party_docs_uploaded(deal, party):
        product = Product.query.get(deal.product_id)
        name = product.title if product else "deal"
        notify_doc_reviewers(deal, f"Documents to check \u00b7 {name}",
                             f"The {party} has uploaded every document. Check and approve them; "
                             "the deal locks when the buyer's and the listing's documents are verified.")
        other = deal.seller_id if party == "buyer" else deal.buyer_id
        notify(other, f"The {party} uploaded their documents \u00b7 {name}"[:200],
               f"The {party} has uploaded their documents. They're checked by DealBriz or the "
               "lender. The deal locks once everything is verified.", deal.product_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        doc_storage.delete(key)
        return _err(RACE_MESSAGE, 409)
    if old_key and old_key != key:
        doc_storage.delete(old_key)
    return jsonify(deal_detail(deal, user)), 201


@bp.route("/<deal_id>/documents/<doc_id>/file", methods=["GET"])
@jwt_required()
def view_document(deal_id, doc_id):
    """A superadmin or the deal's own lender, nobody else (not other staff,
    not the buyer or seller). These are identity documents."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = Deal.query.get(deal_id)
    if not deal or not can_review_docs(user, deal):
        return _err("Not found.", 404)
    doc = DealDocument.query.filter_by(id=doc_id, deal_id=deal.id).first()
    if not doc:
        return _err("Not found.", 404)
    if not doc.storage_key:
        return _err("This file was deleted after the deal closed.", 410)
    if doc_storage.is_s3():
        resp = redirect(doc_storage.view_link(doc.storage_key, doc.file_name, doc.content_type), 302)
    else:
        try:
            resp = send_file(doc_storage.local_path(doc.storage_key), mimetype=doc.content_type,
                             download_name=doc.file_name or "document", as_attachment=False)
        except (FileNotFoundError, ValueError):
            return _err("The file is missing.", 404)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<deal_id>/documents/<doc_id>/review", methods=["POST"])
@jwt_required()
def review_document(deal_id, doc_id):
    """{decision: "approve" | "reject", reason (needed to reject)}.
    A superadmin or the deal's own lender. When every needed document of
    both sides is approved, the deal locks."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or not can_review_docs(user, deal):
        return _err("Not found.", 404)
    if deal.status != DEAL_DOCS_PENDING:
        return _err("Documents can only be checked while the deal is waiting for them.", 409)
    doc = DealDocument.query.filter_by(id=doc_id, deal_id=deal.id).with_for_update().first()
    if not doc:
        return _err("Not found.", 404)
    data = _body()
    decision = data.get("decision")
    reason = (data.get("reason") or "").strip()[:255]
    if decision not in ("approve", "reject"):
        return _err("decision must be approve or reject.", 400)
    if decision == "reject" and not reason:
        return _err("Say what is wrong, so they can fix it.", 400)
    if doc.status != DOC_UPLOADED:
        return _err(f"This document is already {doc.status}.", 409)

    now = utcnow()
    doc.status = DOC_APPROVED if decision == "approve" else DOC_REJECTED
    doc.reviewed_by = user.id
    doc.reviewed_at = now
    doc.reject_reason = reason if decision == "reject" else None
    # A rejected file isn't kept: it may be exactly what must not be stored
    # (a full, unmasked Aadhaar, someone else's PAN). The row keeps the
    # reason; the seller uploads a new file.
    rejected_key = None
    if decision == "reject" and doc.storage_key:
        rejected_key = doc.storage_key
        doc.storage_key = None
        doc.deleted_at = now
    by = "staff" if is_superadmin(user) else "lender"
    add_event(deal, "document_" + ("approved" if decision == "approve" else "rejected"),
              user.id, doc_type=doc.doc_type, reason=doc.reject_reason, by=by)

    outbox = Outbox()
    label = rules.DOC_TYPES.get(doc.doc_type, {}).get("label", doc.doc_type)
    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    if decision == "reject":
        owner = deal.buyer_id if rules.DOC_PARTY.get(doc.doc_type) == "buyer" else deal.seller_id
        other = deal.seller_id if owner == deal.buyer_id else deal.buyer_id
        text = f"{label} needs to be uploaded again: {reason}"
        notify(owner, f"Document needs fixing \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(owner, f"Document needs fixing \u00b7 {title}", text, deal.product_id, other)
    elif docs_all_approved(deal):
        lock_deal(deal, user.id, outbox)

    failed = _commit_or_conflict()
    if failed:
        return failed
    if rejected_key:
        doc_storage.delete(rejected_key)   # only after the commit
    outbox.send()
    return jsonify(deal_detail(deal, user))


# ==========================================================================
# Handover meeting (design: "Handover meeting")
# ==========================================================================

HANDOVER_STEPS = ("down-payment", "ready", "money-received", "vehicle-received", "code")
HANDOVER_CODE_TRIES = 5


@bp.route("/<deal_id>/handover/<step>", methods=["POST"])
@jwt_required()
def handover(deal_id, step):
    """The meeting, in the design's order:
        1. buyer pays the down payment to the seller by UPI and records it
        2. forms signed; both tap Ready  -> lender is asked to pay the seller
        3. (the lender or staff marks Paid with the UTR)
        4. seller checks the money arrived and hands over the vehicle/item
        5. EITHER the seller types in the buyer's 6-digit handover code
           ("code", {code}), which proves the buyer was there and got it,
           OR the seller taps money-received and the buyer vehicle-received.
           Either way -> Completed, listing sold.
    No money passes through DealBriz at any step; these only record what
    happened so both sides and the lender see the same state."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    if step not in HANDOVER_STEPS:
        return _err("Unknown handover step.", 404)
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return _err("Deal not found.", 404)

    is_buyer = user.id == deal.buyer_id
    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    other_id = deal.seller_id if is_buyer else deal.buyer_id
    now = utcnow()
    outbox = Outbox()

    if step == "down-payment":
        if not is_buyer:
            return _err("The buyer records the down payment.", 403)
        if deal.status != DEAL_READY_TO_PAY:
            return _err("The down payment is recorded at the handover meeting, once the "
                        "loan is Ready to pay.", 409)
        if deal.buyer_ready_at:
            return _err("You've already tapped Ready, so the down payment can't be changed.", 409)
        raw = _body().get("amount")
        if raw in (0, "0"):
            amount = 0
        else:
            amount, problem = rules.parse_price(raw)
            if problem:
                return _err(problem.replace("price", "down payment"), 400)
        if amount > deal.agreed_price:
            return _err("The down payment can't be more than the agreed price.", 400)
        deal.down_payment = amount
        deal.down_payment_at = now
        add_event(deal, "down_payment_recorded", user.id, amount=float(amount))
        text = f"Buyer recorded a down payment of {inr(amount)} paid to the seller by UPI."
        post_thread_message(deal.product_id, user.id, other_id, text, "system")
        notify(other_id, f"Down payment recorded \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(other_id, f"{display_name(user)} \u00b7 {title}", text, deal.product_id, user.id)

    elif step == "ready":
        if deal.status != DEAL_READY_TO_PAY:
            return _err("Ready is tapped at the handover meeting, once the loan is Ready to pay.", 409)
        if is_buyer and not deal.down_payment_at:
            return _err("Record the down payment first (enter 0 if there isn't one).", 409)
        if (is_buyer and deal.buyer_ready_at) or (not is_buyer and deal.seller_ready_at):
            return _err("You've already tapped Ready.", 409)
        if is_buyer:
            deal.buyer_ready_at = now
        else:
            deal.seller_ready_at = now
        add_event(deal, "ready", user.id, role="buyer" if is_buyer else "seller")
        if deal.buyer_ready_at and deal.seller_ready_at:
            lender = financer_name(deal.financer_id) or "the lender"
            keep = ("Keys, RC and forms stay" if deal.loan_type == LOAN_VEHICLE
                    else "The item stays")
            text = (f"Both sides are ready. DealBriz has asked {lender} to pay the seller. "
                    f"{keep} with the seller until the money arrives.")
            add_event(deal, "payout_requested", None)
            post_thread_message(deal.product_id, user.id, other_id, text, "system")
            notify(other_id, f"Payout requested \u00b7 {title}"[:200], text, deal.product_id)
            outbox.push(other_id, f"Payout requested \u00b7 {title}", text, deal.product_id, user.id)
            notify_staff(f"Payout needed \u00b7 {title}",
                         f"Buyer and seller are both ready at the handover. Ask {lender} to pay "
                         f"the seller, then mark the deal Paid with the UTR.", deal.product_id)
            if deal.financer_id:
                from .service import notify_lender
                notify_lender(deal.financer_id, f"Payout needed \u00b7 {title}",
                              "Buyer and seller are both ready at the handover. Pay the seller, "
                              "then mark the deal Paid with the UTR.", deal.product_id)
        else:
            who = "Buyer" if is_buyer else "Seller"
            text = f"{who} is ready at the handover."
            post_thread_message(deal.product_id, user.id, other_id, text, "system")
            outbox.push(other_id, f"{display_name(user)} \u00b7 {title}", text, deal.product_id, user.id)

    elif step == "money-received":
        if is_buyer:
            return _err("The seller confirms the money arrived.", 403)
        if deal.status != DEAL_PAID:
            return _err("This is confirmed after the lender has paid you.", 409)
        if deal.money_received_at:
            return _err("Already confirmed.", 409)
        deal.money_received_at = now
        add_event(deal, "money_received", user.id)
        if deal.loan_type == LOAN_VEHICLE:
            text = ("Seller confirmed the money arrived. Collect the keys, original RC and the "
                    "signed forms, then confirm you've received the vehicle.")
        else:
            text = ("Seller confirmed the money arrived. Collect the item, then confirm "
                    "you've received it.")
        post_thread_message(deal.product_id, user.id, other_id, text, "system")
        notify(other_id, f"Money received by seller \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(other_id, f"{display_name(user)} \u00b7 {title}", text, deal.product_id, user.id)

    elif step == "vehicle-received":
        if not is_buyer:
            return _err("The buyer confirms the vehicle was received.", 403)
        if deal.status != DEAL_PAID:
            return _err("This is confirmed after the lender has paid the seller.", 409)
        if not deal.money_received_at:
            return _err("Wait for the seller to confirm the money arrived first.", 409)
        deal.vehicle_received_at = now
        add_event(deal, "vehicle_received", user.id)
        complete_deal(deal, user.id, outbox)

    else:  # code: the seller types in the buyer's handover code
        if is_buyer:
            return _err("The seller enters your code. Show it to the seller at the handover.", 403)
        if deal.status != DEAL_PAID:
            return _err("The handover code is entered after the lender has paid you.", 409)
        code = "".join(ch for ch in str(_body().get("code") or "") if ch.isdigit())
        if len(code) != 6:
            return _err("Enter the buyer's 6-digit handover code.", 400)
        import hmac
        if not deal.handover_code or not hmac.compare_digest(code, deal.handover_code):
            deal.handover_code_attempts = (deal.handover_code_attempts or 0) + 1
            left = HANDOVER_CODE_TRIES - deal.handover_code_attempts
            if left <= 0:
                # Start again with a new code only the buyer can see.
                deal.handover_code = new_handover_code()
                deal.handover_code_attempts = 0
                add_event(deal, "handover_code_reset", None)
                text = ("The handover code was entered wrongly too many times, so it was "
                        "changed. Buyer: open the deal to see the new code.")
                post_thread_message(deal.product_id, user.id, other_id, text, "system")
                notify(other_id, f"New handover code \u00b7 {title}"[:200], text, deal.product_id)
                outbox.push(other_id, f"New handover code \u00b7 {title}", text, deal.product_id, user.id)
                db.session.commit()
                outbox.send()
                return _err("Wrong code too many times. The buyer now has a new code.", 400)
            db.session.commit()
            return _err(f"That isn't the buyer's code. {left} {'try' if left == 1 else 'tries'} left.", 400)
        deal.money_received_at = deal.money_received_at or now
        deal.vehicle_received_at = now
        add_event(deal, "handover_code_confirmed", user.id)
        complete_deal(deal, user.id, outbox)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict(viewer_id=user.id))


@bp.route("/<deal_id>/dispute", methods=["POST"])
@jwt_required()
def raise_dispute(deal_id):
    """Design: after Paid there is no release; a problem goes to the staff
    queue as a dispute."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return _err("Deal not found.", 404)
    if deal.status != DEAL_PAID:
        if deal.status in RELEASABLE_STATES:
            return _err("Before the payout, cancel the deal instead.", 409)
        return _err(f"This deal is {deal.status}; it can't be disputed.", 409)
    reason = (_body().get("reason") or "").strip()
    if not reason:
        return _err("Please say what went wrong.", 400)
    reason = reason[:rules.MAX_CANCEL_NOTE_CHARS]

    deal.status = DEAL_DISPUTED
    deal.disputed_at = utcnow()
    add_event(deal, "disputed", user.id, reason=reason)
    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    other_id = deal.seller_id if user.id == deal.buyer_id else deal.buyer_id
    text = "A problem with this deal was reported to DealBriz support, who will contact both of you."
    post_thread_message(deal.product_id, user.id, other_id, text, "system")
    outbox = Outbox()
    notify(other_id, f"Deal under review \u00b7 {title}"[:200], text, deal.product_id)
    outbox.push(other_id, f"Deal under review \u00b7 {title}", text, deal.product_id, user.id)
    notify_staff(f"Dispute \u00b7 {title}", f"Raised by {display_name(user)}: {reason}", deal.product_id)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict(viewer_id=user.id))


# ==========================================================================
# Staff: the lender-side steps, by hand, until a lender has an API
# ==========================================================================

def _staff_row(deal, products, users):
    d = deal.to_dict()
    d["product"] = _product_summary(products.get(deal.product_id))
    for role in ("buyer", "seller"):
        u = users.get(getattr(deal, f"{role}_id"))
        d[role] = ({"id": u.id, "name": display_name(u), "email": u.email, "phone": u.phone}
                   if u else None)
    d["loan"] = loan_summary(deal, None, is_staff=True)
    d["lender"] = financer_name(deal.financer_id)
    if deal.status == DEAL_DOCS_PENDING:
        checklist = docs_checklist(deal)
        need = [c for c in checklist if c["required"]]
        d["docs_summary"] = {
            "needed": len(need),
            "to_check": sum(1 for c in need if c["status"] == DOC_UPLOADED),
            "approved": sum(1 for c in need if c["status"] == DOC_APPROVED),
            "missing": sum(1 for c in need if c["status"] in ("missing", DOC_REJECTED)),
        }
    return d


@bp.route("/staff/queue", methods=["GET"])
@require_capability("deals.view")
def staff_queue():
    """?status=<one deal status>, or by default every deal that is still
    moving (awaiting seller through disputed)."""
    status = (request.args.get("status") or "").strip()
    q = Deal.query
    if status:
        q = q.filter(Deal.status == status)
    else:
        q = q.filter(Deal.status.in_(LIVE_DEAL_STATES))
    deals = q.order_by(Deal.updated_at.desc()).limit(200).all()
    pids = {d.product_id for d in deals}
    uids = {d.buyer_id for d in deals} | {d.seller_id for d in deals}
    products = {p.id: p for p in Product.query.filter(Product.id.in_(pids)).all()} if pids else {}
    users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
    return jsonify([_staff_row(d, products, users) for d in deals])


def _staff_deal(deal_id):
    return Deal.query.filter_by(id=deal_id).with_for_update().first()


@bp.route("/staff/<deal_id>/financer", methods=["POST"])
@require_capability("deals.manage")
def staff_set_financer(deal_id):
    """{financer_id} or {financer_id: null}. Which lender handles this deal,
    so that lender's people can check the documents and the loan. Only
    before the loan is applied for; after that, change it on the EMI
    application."""
    staff = current_user()
    deal = _staff_deal(deal_id)
    if not deal:
        return _err("Deal not found.", 404)
    if deal.status not in (DEAL_AWAITING_SELLER, DEAL_DOCS_PENDING, DEAL_LOCKED):
        return _err("The lender can only be changed here before the loan is applied for.", 409)
    fid = _body().get("financer_id")
    if fid in (None, "", 0):
        deal.financer_id = None
        name = None
    else:
        f = Financer.query.get(fid)
        if not f:
            return _err("Unknown lender.", 400)
        if not financer_supports(f, deal.loan_type):
            return _err(f"{f.name} doesn't fund {deal.loan_type} loans.", 409)
        deal.financer_id = f.financer_id
        name = f.name
    add_event(deal, "lender_set", staff.id, lender=name)
    failed = _commit_or_conflict()
    if failed:
        return failed
    return jsonify(deal.to_dict())


@bp.route("/staff/<deal_id>/ready-to-pay", methods=["POST"])
@require_capability("deals.manage")
def staff_ready_to_pay(deal_id):
    """The lender has sanctioned the loan, the agreement and EMI auto-debit
    are signed, and the seller's documents and vehicle are checked."""
    staff = current_user()
    deal = _staff_deal(deal_id)
    if not deal:
        return _err("Deal not found.", 404)
    note = (_body().get("note") or "").strip()[:500] or None
    outbox = Outbox()
    problem = mark_ready_to_pay(deal, staff, "staff", note, outbox)
    if problem:
        db.session.rollback()
        return _err(problem, 409)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict())


@bp.route("/staff/<deal_id>/paid", methods=["POST"])
@require_capability("deals.manage")
def staff_mark_paid(deal_id):
    """The lender confirmed it paid the seller. Needs the UTR the lender
    gives for the IMPS/RTGS transfer, which both sides then see."""
    staff = current_user()
    deal = _staff_deal(deal_id)
    if not deal:
        return _err("Deal not found.", 404)
    utr = clean_utr(_body().get("utr"))
    if deal.status == DEAL_READY_TO_PAY and deal.buyer_ready_at and deal.seller_ready_at and not utr:
        return _err("Enter the UTR from the lender (6 to 30 letters and digits).", 400)
    outbox = Outbox()
    problem = mark_paid(deal, staff, "staff", utr, outbox)
    if problem:
        db.session.rollback()
        return _err(problem, 409)
    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict())


@bp.route("/staff/<deal_id>/resolve", methods=["POST"])
@require_capability("deals.manage")
def staff_resolve(deal_id):
    """Close a dispute. outcome:
         completed  the handover did happen (listing sold, purchase recorded)
         paid       back to Paid so the two sides can finish the handover
    Anything involving money back goes through the lender, outside DealBriz."""
    staff = current_user()
    deal = _staff_deal(deal_id)
    if not deal:
        return _err("Deal not found.", 404)
    if deal.status != DEAL_DISPUTED:
        return _err("Only a disputed deal can be resolved.", 409)
    data = _body()
    outcome = data.get("outcome")
    note = (data.get("note") or "").strip()[:500]
    if outcome not in ("completed", "paid"):
        return _err("outcome must be 'completed' or 'paid'.", 400)
    if not note:
        return _err("Add a note saying how it was resolved.", 400)

    outbox = Outbox()
    add_event(deal, "dispute_resolved", staff.id, outcome=outcome, note=note)
    if outcome == "paid":
        deal.status = DEAL_PAID
        text = f"DealBriz support reviewed the problem: {note}. Please finish the handover."
        post_thread_message(deal.product_id, deal.seller_id, deal.buyer_id, text, "system")
        for uid in (deal.buyer_id, deal.seller_id):
            notify(uid, "Deal review finished", text, deal.product_id)
    else:
        deal.money_received_at = deal.money_received_at or utcnow()
        deal.vehicle_received_at = deal.vehicle_received_at or utcnow()
        complete_deal(deal, staff.id, outbox)

    failed = _commit_or_conflict()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict())
