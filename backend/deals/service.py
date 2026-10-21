"""
DealLock logic shared by the deals routes, the timer job, and the guards in
mylistings/ and admin/.

Nothing here commits. Callers commit once, so a deal change, its chat
message, its timeline event and its notifications land together or not at
all. Pushes are collected in an Outbox and only sent after that commit, so a
phone never buzzes for a change that was rolled back.
"""
import logging
import secrets
from datetime import datetime, timedelta

from extensions import db
from models import (Deal, DealDocument, DealEvent, DealOffer, EmiApplication,
                    Financer, FinancerMember, LoanStatusEvent, Message,
                    Notification, Product, Purchase, PurchaseUser, User)
from models.deal import (CONFIRMED_STATES, DEAL_AWAITING_SELLER, DEAL_COMPLETED,
                         DEAL_DOCS_PENDING, DEAL_LOAN_IN_PROGRESS, DEAL_LOCKED,
                         DEAL_PAID, DEAL_READY_TO_PAY, DEAL_RELEASED,
                         DOC_APPROVED, LIVE_DEAL_STATES, LOAN_VEHICLE,
                         OFFER_OPEN, OFFER_EXPIRED, OFFER_WITHDRAWN,
                         FORCE_CANCEL_REASONS, RELEASABLE_STATES,
                         RELEASE_BUYER_FORCE, RELEASE_BUYER_NO_RESPONSE,
                         RELEASE_BUYER_WITHDREW, RELEASE_LOAN_REJECTED,
                         RELEASE_MUTUAL, RELEASE_RESERVATION_EXPIRED,
                         RELEASE_SELLER_CANCELLED, RELEASE_SELLER_DECLINED,
                         RELEASE_SELLER_FORCE, RELEASE_SELLER_NO_RESPONSE,
                         RELEASE_STAFF_ACTION, TIMED_STATES)
from . import rules

log = logging.getLogger("dealbridge.deals")

PRODUCT_RESERVED = "reserved"
PRODUCT_ACTIVE = "active"
PRODUCT_SOLD = "sold"

# EMI application statuses (admin/routes.py EMI_STATUSES) that are still
# open, i.e. the lender hasn't said no and hasn't paid yet.
LOAN_OPEN_STATUSES = ("pending", "review", "approved")
# A loan stuck in Loan in progress this long gets flagged to staff.
STALLED_LOAN_HOURS = 48


def utcnow():
    return datetime.utcnow()


# --------------------------------------------------------------------------
# Small formatting helpers
# --------------------------------------------------------------------------

def inr(amount):
    """340000 -> '₹3,40,000' (Indian digit grouping)."""
    n = int(amount)
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if n < 0 else "") + "₹" + s


def display_name(user):
    if not user:
        return "User"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or "User"


def short_date(dt):
    """For message text only. Shown in IST because every user is in India,
    and a date one day off ("reserved until the 18th" when it ends at 2 AM on
    the 19th) is exactly the kind of thing people screenshot and argue about.
    Clients get the exact UTC timestamp alongside it."""
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime("%d %b, %I:%M %p").lstrip("0").replace(" 0", " ")


# --------------------------------------------------------------------------
# Outbox: pushes to send after commit
# --------------------------------------------------------------------------

class Outbox:
    def __init__(self):
        self._items = []

    def push(self, recipient_id, heading, text, product_id, other_user_id):
        if recipient_id:
            self._items.append((recipient_id, heading, text, product_id, other_user_id))

    def send(self):
        """Call after commit. Returns the started threads (the timer job waits
        on them before exiting; requests ignore them)."""
        from push import send_deal_push
        threads = []
        for item in self._items:
            t = send_deal_push(*item)
            if t is not None:
                threads.append(t)
        self._items = []
        return threads


# --------------------------------------------------------------------------
# Writes that always go together
# --------------------------------------------------------------------------

def post_thread_message(product_id, sender_id, receiver_id, body, kind, offer_id=None):
    """A message in the buyer-seller chat. kind is 'offer' or 'system'.
    body is readable on its own, so clients that don't know `kind` yet still
    show something that makes sense."""
    msg = Message(product_id=product_id, sender_id=sender_id,
                  receiver_id=receiver_id, body=body, kind=kind,
                  offer_id=offer_id)
    db.session.add(msg)
    return msg


def notify(user_id, title, body, product_id):
    """Bell notification. kind 'chat' and page 'mychats' are ones the website
    already renders and can open, so these work before any client update."""
    return Notification.push(user_id, "chat", title, body=body,
                             link_page="mychats", link_ref=product_id)


def add_event(deal, event_type, actor_id=None, **data):
    ev = DealEvent(deal_id=deal.id, event_type=event_type, actor_id=actor_id,
                   event_data=data or None)
    db.session.add(ev)
    return ev


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------

def live_deal_for_product(product_id, lock=False):
    q = Deal.query.filter(Deal.product_id == product_id,
                          Deal.status.in_(LIVE_DEAL_STATES))
    if lock:
        q = q.with_for_update()
    return q.first()


def deal_stage_for_products(products):
    """{product_id: "pending" | "reserved"} for listings in a live deal, in
    one query. "pending": waiting for the seller or documents (not marked
    reserved yet). "reserved": locked or later."""
    ids = [p.id for p in products]
    if not ids:
        return {}
    out = {}
    for pid, status in (db.session.query(Deal.product_id, Deal.status)
                        .filter(Deal.product_id.in_(ids), Deal.status.in_(LIVE_DEAL_STATES)).all()):
        out[pid] = "pending" if status in (DEAL_AWAITING_SELLER, DEAL_DOCS_PENDING) else "reserved"
    return out


def has_chat_thread(product_id, user_a, user_b):
    return db.session.query(Message.id).filter(
        Message.product_id == product_id,
        db.or_(
            db.and_(Message.sender_id == user_a, Message.receiver_id == user_b),
            db.and_(Message.sender_id == user_b, Message.receiver_id == user_a),
        )
    ).first() is not None


def lender_financer_id(user):
    """The lender a lender-portal user works for, or None. Needs both the
    capability (from the role) and a membership row."""
    if not user:
        return None
    from permissions import has_capability
    if not has_capability(user, "lender.portal"):
        return None
    m = FinancerMember.query.get(user.id)
    return m.financer_id if m else None


def can_review_docs(user, deal):
    """Who may open and verify the buyer's and seller's documents: a
    superadmin, or the deal's own lender (NBFC) in its portal. Nobody else,
    not other staff roles, and not the buyer or seller themselves once
    uploaded (they see each file's status, never the file)."""
    if not user:
        return False
    from utils import is_superadmin
    if is_superadmin(user):
        return True
    fid = lender_financer_id(user)
    return fid is not None and deal.financer_id == fid


def can_view_deal(user, deal):
    """The two sides, DealBriz staff, or the deal's own lender."""
    from utils import has_staff_access
    if not user or not deal:
        return False
    if user.id in (deal.buyer_id, deal.seller_id) or has_staff_access(user):
        return True
    from permissions import has_capability
    if has_capability(user, "deals.view"):
        return True
    fid = lender_financer_id(user)
    return fid is not None and deal.financer_id == fid


def deal_records_exist_for_user(user_id):
    """True if hard-deleting this user would break DealLock history. Deal rows
    are kept on purpose (they are the record of what was agreed), so such an
    account must be suspended rather than deleted."""
    offer = db.session.query(DealOffer.id).filter(db.or_(
        DealOffer.buyer_id == user_id, DealOffer.seller_id == user_id,
        DealOffer.from_user_id == user_id)).first()
    if offer:
        return True
    deal = db.session.query(Deal.id).filter(db.or_(
        Deal.buyer_id == user_id, Deal.seller_id == user_id)).first()
    return deal is not None


def deal_records_exist_for_product(product_id):
    return db.session.query(DealOffer.id).filter(
        DealOffer.product_id == product_id).first() is not None


def is_banned(user, now=None):
    now = now or utcnow()
    return bool(user and user.deal_ban_until and user.deal_ban_until > now)


def ban_message(user):
    """What a banned user is told, wherever they hit the ban."""
    return (f"You force-cancelled {rules.CANCEL_LIMIT} deals in a row, so you can't post "
            f"new listings or make new deals until {short_date(user.deal_ban_until)}.")


def own_block_reason(user):
    """None if this user may make offers and deals right now."""
    if not user or (user.account_status or "active") != "active":
        return "Your account is not active."
    if is_banned(user):
        return ban_message(user)
    return None


def party_block_reason(user, who):
    """None if the other side (who = 'buyer' or 'seller') may take part in a
    new deal right now. Doesn't say why a ban exists: that is theirs."""
    if not user:
        return f"The {who}'s account no longer exists."
    if (user.account_status or "active") != "active":
        return f"The {who}'s account is not active."
    if is_banned(user):
        return f"The {who} can't make new deals right now."
    return None


def listing_blocked_reason(product):
    """None if offers can be made on this listing right now."""
    if not product:
        return "That listing no longer exists."
    if not rules.category_allowed(product.category):
        return "EMI deals aren't available for this category yet."
    if product.status == PRODUCT_RESERVED:
        return "This listing is reserved for another buyer."
    if product.status != PRODUCT_ACTIVE:
        return "This listing is no longer available."
    return None


# --------------------------------------------------------------------------
# State changes
# --------------------------------------------------------------------------

def release_deal(deal, reason, actor_id, outbox, note=None):
    """Release a live deal (any state before Paid). Puts the listing back on
    sale, withdraws the loan application, clears any cancel request, tells
    both sides, and, for a FORCE cancel, counts it towards the one who
    force-cancelled's pause (rules.CANCEL_LIMIT in a row).

    Returns True if it released, False if the deal was not releasable (the
    caller decides whether that's an error)."""
    if deal.status not in RELEASABLE_STATES:
        return False

    now = utcnow()
    was = deal.status
    deal.status = DEAL_RELEASED
    deal.released_at = now
    deal.released_by = actor_id
    deal.release_reason = reason
    deal.release_note = (note or "")[:rules.MAX_CANCEL_NOTE_CHARS] or None

    deal.clear_cancel_request()

    product = Product.query.get(deal.product_id)
    if product is not None and product.status == PRODUCT_RESERVED:
        product.status = PRODUCT_ACTIVE

    # Only a FORCE cancel counts. Backing out before the seller confirms, or
    # cancelling with the other side's agreement, never does.
    counted = None
    if reason in FORCE_CANCEL_REASONS:
        canceller_id = deal.buyer_id if reason == RELEASE_BUYER_FORCE else deal.seller_id
        counted = count_cancel(canceller_id, now, outbox, deal.product_id)

    add_event(deal, "released", actor_id, reason=reason, note=deal.release_note,
              previous_status=was,
              cancel_count=counted["count"] if counted else None,
              banned_until=counted["banned_until"] if counted else None)

    # Design: on release the lender application is withdrawn. A rejected
    # one is already closed and is left as it is.
    for app in EmiApplication.query.filter(EmiApplication.deal_id == deal.id,
                                           EmiApplication.status.in_(LOAN_OPEN_STATUSES)).all():
        app.status = "cancelled"
        app.closed_at = now
        add_loan_event(app, "cancelled", "system", None,
                       f"Withdrawn because the deal was released ({reason}).")

    title = product.title if product else "your listing"
    if reason == RELEASE_BUYER_WITHDREW:
        line = "The buyer withdrew from the deal before the seller confirmed it."
    elif reason == RELEASE_MUTUAL:
        line = "Both sides agreed to cancel the deal."
    elif reason == RELEASE_BUYER_FORCE:
        line = "The buyer force-cancelled the deal."
    elif reason == RELEASE_SELLER_FORCE:
        line = "The seller force-cancelled the deal."
    elif reason == RELEASE_BUYER_NO_RESPONSE:
        line = (f"The buyer didn't accept the deal warning within {rules.CONFIRM_HOURS} hours, "
                "so the deal was released.")
    elif reason == RELEASE_SELLER_CANCELLED:
        line = "The seller cancelled the deal."
    elif reason == RELEASE_SELLER_DECLINED:
        line = "The seller decided not to lock this deal."
    elif reason == RELEASE_SELLER_NO_RESPONSE:
        line = (f"The seller didn't confirm within {rules.CONFIRM_HOURS} hours, "
                "so the deal was released.")
    elif reason == RELEASE_RESERVATION_EXPIRED:
        if was == DEAL_DOCS_PENDING:
            line = (f"The documents weren't all uploaded and verified within {rules.DOCS_DAYS} "
                    "days, so the deal was released.")
        else:
            line = (f"The {rules.RESERVATION_DAYS}-day reservation ended before the "
                    "loan was ready, so the deal was released.")
    elif reason == RELEASE_STAFF_ACTION:
        line = "DealBriz support released this deal."
    elif reason == RELEASE_LOAN_REJECTED:
        line = "The loan was not approved, so the deal was released."
    else:
        line = "This deal was released."
    body = f"{line} The listing is available again."
    if deal.release_note and reason in (RELEASE_BUYER_WITHDREW, RELEASE_SELLER_CANCELLED,
                                        RELEASE_SELLER_DECLINED, RELEASE_MUTUAL,
                                        RELEASE_BUYER_FORCE, RELEASE_SELLER_FORCE):
        body += f" Reason: {deal.release_note}"

    # The thread message goes from whoever acted to the other side. For
    # system releases (timer, staff) it is written as from the seller to the
    # buyer, since a message needs both ends; its kind marks it as system.
    if actor_id == deal.buyer_id:
        sender, receiver = deal.buyer_id, deal.seller_id
    else:
        sender, receiver = deal.seller_id, deal.buyer_id
    post_thread_message(deal.product_id, sender, receiver, body, "system")

    for uid, other in ((deal.buyer_id, deal.seller_id), (deal.seller_id, deal.buyer_id)):
        if uid == actor_id:
            continue
        notify(uid, f"Deal released \u00b7 {title}"[:200], body, deal.product_id)
        outbox.push(uid, f"Deal released \u00b7 {title}", body, deal.product_id, other)
    return True


def count_cancel(user_id, now, outbox, product_id=None):
    """+1 to the user's force cancels in a row. At rules.CANCEL_LIMIT they are
    banned for rules.BAN_DAYS (no new listings, no new deals), the count
    starts again, and their open offers are withdrawn. Returns
    {"count", "banned_until"}."""
    user = User.query.filter_by(id=user_id).with_for_update().first()
    if user is None:
        return None
    user.consecutive_cancels = (user.consecutive_cancels or 0) + 1
    count = user.consecutive_cancels
    banned_until = None
    if count >= rules.CANCEL_LIMIT:
        user.deal_ban_until = now + timedelta(days=rules.BAN_DAYS)
        user.consecutive_cancels = 0
        banned_until = user.deal_ban_until.isoformat() + "Z"
        for offer in (DealOffer.query
                      .filter(DealOffer.status == OFFER_OPEN, DealOffer.from_user_id == user.id)
                      .with_for_update().all()):
            offer.status = OFFER_WITHDRAWN
            offer.responded_at = now
        notify(user.id, f"New listings and deals paused for {rules.BAN_DAYS} days",
               ban_message(user) + " Contact support if this is a mistake.", product_id)
    elif count == rules.CANCEL_LIMIT - 1:
        notify(user.id, "Careful: one more force cancel pauses your account",
               f"You have force-cancelled {count} deals in a row. If you force-cancel one more, "
               f"you can't post listings or make deals for {rules.BAN_DAYS} days.", product_id)
    return {"count": count, "banned_until": banned_until}


# --------------------------------------------------------------------------
# Cancelling a confirmed deal: request, agree / disagree, force (phase 4)
# --------------------------------------------------------------------------

def _other_side(deal, user_id):
    return deal.seller_id if user_id == deal.buyer_id else deal.buyer_id


def _role(deal, user_id):
    return "buyer" if user_id == deal.buyer_id else "seller"


def _tell_both(deal, actor_id, title, text, outbox):
    """Chat line from the actor to the other side, plus a bell and a push
    for the other side."""
    product = Product.query.get(deal.product_id)
    name = product.title if product else "the listing"
    other = _other_side(deal, actor_id)
    post_thread_message(deal.product_id, actor_id, other, text, "system")
    notify(other, f"{title} \u00b7 {name}"[:200], text, deal.product_id)
    outbox.push(other, f"{title} \u00b7 {name}", text, deal.product_id, actor_id)


def request_cancel(deal, user, reason, outbox):
    """The buyer or seller asks to cancel a confirmed deal. The other side
    has CANCEL_REQUEST_HOURS to agree or disagree. Returns an error string
    or None."""
    if deal.status not in CONFIRMED_STATES:
        return f"This deal is {deal.status.replace('_', ' ')}, so it can't be cancelled this way."
    if deal.has_cancel_request:
        if deal.cancel_requested_by == user.id:
            return "You have already asked to cancel. Wait for the other side, or force-cancel."
        return "The other side has already asked to cancel. Agree or disagree with their request."
    now = utcnow()
    deal.cancel_requested_by = user.id
    deal.cancel_requested_at = now
    deal.cancel_request_expires_at = now + timedelta(hours=rules.CANCEL_REQUEST_HOURS)
    deal.cancel_request_reason = (reason or "")[:rules.MAX_CANCEL_NOTE_CHARS] or None
    who = _role(deal, user.id)
    add_event(deal, "cancel_requested", user.id, by=who, reason=deal.cancel_request_reason,
              expires_at=deal.cancel_request_expires_at.isoformat() + "Z")
    text = (f"The {who} asked to cancel this deal."
            + (f" Reason: {deal.cancel_request_reason}" if deal.cancel_request_reason else "")
            + f" Open the deal to agree or disagree by {short_date(deal.cancel_request_expires_at)}.")
    _tell_both(deal, user.id, "Cancel request", text, outbox)
    return None


def answer_cancel(deal, user, agree, outbox):
    """The other side answers an open cancel request. Agree releases the
    deal (nobody is penalised). Disagree closes the request, and the one who
    asked can ask again or force-cancel. Returns an error string or None."""
    if not deal.has_cancel_request:
        return "There is no open cancel request on this deal."
    if deal.cancel_requested_by == user.id:
        return "You asked to cancel. The other side answers it."
    note = deal.cancel_request_reason
    who = _role(deal, user.id)
    if agree:
        add_event(deal, "cancel_agreed", user.id, by=who)
        release_deal(deal, RELEASE_MUTUAL, user.id, outbox, note=note)
        return None
    deal.clear_cancel_request()
    add_event(deal, "cancel_disagreed", user.id, by=who)
    text = (f"The {who} disagreed with cancelling, so the deal carries on. "
            "The one who asked can ask again or force-cancel.")
    _tell_both(deal, user.id, "Cancel request declined", text, outbox)
    return None


def withdraw_cancel(deal, user, outbox):
    if not deal.has_cancel_request or deal.cancel_requested_by != user.id:
        return "You have no open cancel request on this deal."
    deal.clear_cancel_request()
    who = _role(deal, user.id)
    add_event(deal, "cancel_withdrawn", user.id, by=who)
    _tell_both(deal, user.id, "Cancel request withdrawn",
               f"The {who} withdrew their cancel request. The deal carries on.", outbox)
    return None


def force_cancel(deal, user, outbox):
    """The one who asked ends the deal without the other side's agreement.
    Counts towards the pause: rules.CANCEL_LIMIT force cancels in a row stop
    new listings and deals for rules.BAN_DAYS. Returns (error, None) or
    (None, {"count", "banned_until"}) for the one who force-cancelled."""
    if not deal.has_cancel_request or deal.cancel_requested_by != user.id:
        return "Ask to cancel first. Force cancel is available while your request is open.", None
    note = deal.cancel_request_reason
    reason = RELEASE_BUYER_FORCE if user.id == deal.buyer_id else RELEASE_SELLER_FORCE
    add_event(deal, "force_cancelled", user.id, by=_role(deal, user.id))
    if not release_deal(deal, reason, user.id, outbox, note=note):
        return "This deal can no longer be cancelled.", None
    me = User.query.get(user.id)
    return None, {"count": me.consecutive_cancels or 0,
                  "banned_until": (me.deal_ban_until.isoformat() + "Z"
                                   if me.deal_ban_until and me.deal_ban_until > utcnow() else None)}


def expire_cancel_requests(now, outbox, batch=200):
    """Requests nobody answered within CANCEL_REQUEST_HOURS lapse. The one
    who asked is told they can ask again or force-cancel."""
    deals = (Deal.query
             .filter(Deal.cancel_requested_by.isnot(None),
                     Deal.cancel_request_expires_at <= now)
             .order_by(Deal.cancel_request_expires_at)
             .limit(batch)
             .with_for_update(skip_locked=True)
             .all())
    for deal in deals:
        requester = deal.cancel_requested_by
        deal.clear_cancel_request()
        if deal.status not in CONFIRMED_STATES or requester is None:
            continue
        add_event(deal, "cancel_request_expired", None)
        product = Product.query.get(deal.product_id)
        name = product.title if product else "the listing"
        text = (f"Nobody answered your cancel request within {rules.CANCEL_REQUEST_HOURS} hours, "
                "so it lapsed and the deal carries on. You can ask again, and force-cancel "
                "while that request is open.")
        notify(requester, f"Cancel request lapsed \u00b7 {name}"[:200], text, deal.product_id)
        outbox.push(requester, f"Cancel request lapsed \u00b7 {name}", text, deal.product_id,
                    _other_side(deal, requester))
    return len(deals)


def release_live_deals_for_user(user_id, reason, outbox, actor_id=None):
    """Used when an account is suspended: release every deal it is part of
    that can still be released, and withdraw its open offers. Nothing is
    counted towards a ban, because neither side chose this."""
    released = 0
    deals = (Deal.query
             .filter(Deal.status.in_(RELEASABLE_STATES),
                     db.or_(Deal.buyer_id == user_id, Deal.seller_id == user_id))
             .with_for_update().all())
    for deal in deals:
        if release_deal(deal, reason, actor_id, outbox):
            released += 1

    now = utcnow()
    offers = (DealOffer.query
              .filter(DealOffer.status == OFFER_OPEN,
                      db.or_(DealOffer.buyer_id == user_id, DealOffer.seller_id == user_id))
              .with_for_update().all())
    for offer in offers:
        offer.status = OFFER_WITHDRAWN
        offer.responded_at = now
    return released, len(offers)


def guard_listing_status_change(product, new_status, actor, by_staff, outbox):
    """Called before any edit that sets products.status.

    Returns an error string to send back as 409/400, or None to go ahead.
    - Nobody may set 'reserved' by hand; only a locked deal does that.
    - A seller can't change the status of a reserved listing. They have to
      cancel the deal first, where the app warns them it counts towards the
      ban. (The design also lets "remove listing" release the deal, but
      today's app and website send that without any warning, so it is
      blocked until the clients show one.)
    - Staff can: the deal is released with reason staff_action, not counted.
    """
    if new_status is None or new_status == product.status:
        return None
    if new_status == PRODUCT_RESERVED:
        return "A listing becomes reserved only when a buyer and seller lock a deal."

    # Any live deal protects the listing, including one still waiting for
    # the seller or for documents (the listing isn't marked reserved yet).
    deal = live_deal_for_product(product.id, lock=True)
    if deal is None:
        return None
    if not by_staff:
        return ("A buyer is in a deal on this listing. End the deal first "
                "(from the deal, in your chat or My Deals).")
    if not release_deal(deal, RELEASE_STAFF_ACTION, actor.id if actor else None, outbox,
                        note="Listing changed by DealBriz support"):
        return ("This deal has already been paid out, so the listing can't be "
                "changed. Handle it as a dispute.")
    return None


# --------------------------------------------------------------------------
# Seller confirmation, documents, handover code (phase 3)
# --------------------------------------------------------------------------

def new_handover_code():
    return f"{secrets.randbelow(1000000):06d}"


def active_financers_for(loan_type):
    return [f for f in Financer.query.filter_by(status="active").all()
            if loan_type in f.loan_type_list()]


def financer_supports(financer, loan_type):
    return financer is not None and loan_type in financer.loan_type_list()


def auto_financer_id(loan_type):
    """The lender to use when exactly one active lender funds this loan
    type. With several, staff choose on the loan application."""
    matches = active_financers_for(loan_type)
    return matches[0].financer_id if len(matches) == 1 else None


def deal_documents(deal):
    return {d.doc_type: d for d in DealDocument.query.filter_by(deal_id=deal.id).all()}


def deal_product(deal):
    return Product.query.get(deal.product_id)


def docs_checklist(deal, product=None):
    """[{type, label, hint, party, source, required, status, document}] in
    display order. Phase 5: the seller's rows come from the LISTING
    (source "listing", checked once for every deal on it), the buyer's from
    the deal (source "deal"). status: missing / uploaded / approved /
    rejected."""
    from . import listing_docs as ld
    product = product or deal_product(deal)
    out = ld.checklist(product) if product is not None else []
    have = deal_documents(deal)
    need = rules.required_buyer_docs()
    for doc_type in rules.BUYER_DOC_TYPES:
        doc = have.get(doc_type)
        if doc_type not in need and doc is None:
            continue
        meta = rules.DOC_TYPES[doc_type]
        out.append({
            "type": doc_type, "label": meta["label"], "hint": meta["hint"],
            "party": "buyer", "source": "deal",
            "required": doc_type in need,
            "status": doc.status if doc else "missing",
            "document": doc.to_dict() if doc else None,
        })
    return out


def docs_question_pending(deal, product=None):
    """The seller still has to say whether the vehicle had a loan (asked on
    the listing) before its list of documents is final."""
    from . import listing_docs as ld
    product = product or deal_product(deal)
    return product is not None and ld.question_pending(product)


def buyer_docs_approved(deal):
    have = deal_documents(deal)
    return all(t in have and have[t].status == DOC_APPROVED for t in rules.required_buyer_docs())


def docs_all_approved(deal):
    """Everything this deal waits for is verified: the buyer's documents in
    the deal and the seller's on the listing."""
    from . import listing_docs as ld
    product = deal_product(deal)
    if product is not None and not ld.verified(product):
        return False
    return buyer_docs_approved(deal)


def party_docs_uploaded(deal, party):
    """True when this side has uploaded everything it must (each file
    uploaded or already approved). The seller's side is the listing's."""
    from . import listing_docs as ld
    if party == "seller":
        product = deal_product(deal)
        return product is None or ld.all_uploaded(product)
    have = deal_documents(deal)
    return all(t in have and have[t].status in ("uploaded", DOC_APPROVED)
               for t in rules.required_buyer_docs())


def lock_deal(deal, actor_id, outbox):
    """docs_pending -> locked, once the buyer's documents and the listing's
    are verified. Only now is the listing marked Reserved, and the
    RESERVATION_DAYS reservation (the time for the loan) starts."""
    from . import listing_docs as ld
    now = utcnow()
    deal.status = DEAL_LOCKED
    product = Product.query.get(deal.product_id)
    if rules.required_buyer_docs() or (product is not None and ld.needs_docs(product)):
        deal.docs_approved_at = now
    deal.reserved_until = now + timedelta(days=rules.RESERVATION_DAYS)
    add_event(deal, "locked", actor_id, reserved_until=deal.reserved_until.isoformat() + "Z")
    if product is not None and product.status == PRODUCT_ACTIVE:
        product.status = PRODUCT_RESERVED
    title = product.title if product else "the listing"
    text = (f"Deal locked at {inr(deal.agreed_price)}. The listing is reserved until "
            f"{short_date(deal.reserved_until)}. Buyer: apply for the loan now.")
    post_thread_message(deal.product_id, deal.seller_id, deal.buyer_id, text, "system")
    for uid, other in ((deal.buyer_id, deal.seller_id), (deal.seller_id, deal.buyer_id)):
        notify(uid, f"Deal locked \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(uid, f"Deal locked \u00b7 {title}", text, deal.product_id, other)


def confirm_deal(deal, seller, outbox):
    """The seller confirmed (the buyer accepted the warning first, the
    seller ticked theirs and the password or SMS code was checked by the
    caller). The buyer now uploads their documents within DOCS_DAYS
    (reserved_until holds that deadline until the deal locks). The seller's
    are on the listing: if they aren't uploaded yet, the seller is sent to
    Edit listing. A deal with nothing left to check locks now."""
    from . import listing_docs as ld
    now = utcnow()
    deal.seller_confirmed_at = now
    deal.seller_ack_at = now
    deal.reserved_until = now + timedelta(days=rules.DOCS_DAYS)
    deal.confirm_by = None
    if not deal.handover_code:
        deal.handover_code = new_handover_code()
        deal.handover_code_attempts = 0
    if not deal.financer_id:
        deal.financer_id = auto_financer_id(deal.loan_type)
    add_event(deal, "seller_confirmed", seller.id,
              reserved_until=deal.reserved_until.isoformat() + "Z")

    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    if product is not None and product.vehicle_had_loan is not None and deal.vehicle_had_loan is None:
        deal.vehicle_had_loan = product.vehicle_had_loan
    listing_state = ld.state(product) if product is not None else ld.STATE_NONE
    buyer_docs = bool(rules.required_buyer_docs())
    if not buyer_docs and listing_state in (ld.STATE_NONE, ld.STATE_VERIFIED):
        lock_deal(deal, seller.id, outbox)
        return

    deal.status = DEAL_DOCS_PENDING
    due = short_date(deal.reserved_until)
    parts = ["The seller confirmed the deal."]
    if buyer_docs:
        parts.append(f"Buyer: upload your PAN card and masked Aadhaar in the deal by {due}.")
    if listing_state == ld.STATE_NEEDED:
        parts.append(f"Seller: upload your vehicle documents on the listing (My Listings, "
                     f"Edit listing) by {due}.")
    elif listing_state == ld.STATE_CHECKING:
        parts.append("The seller's vehicle documents are being checked.")
    parts.append("The deal locks, and the listing is reserved for the buyer, once DealBriz or "
                 "the lender has verified everything.")
    text = " ".join(parts)
    post_thread_message(deal.product_id, seller.id, deal.buyer_id, text, "system")
    if buyer_docs:
        notify(deal.buyer_id, f"Upload your documents \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(deal.buyer_id, f"Upload your documents \u00b7 {title}", text,
                    deal.product_id, seller.id)
    else:
        notify(deal.buyer_id, f"Seller confirmed \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(deal.buyer_id, f"Seller confirmed \u00b7 {title}", text, deal.product_id, seller.id)
    if listing_state == ld.STATE_NEEDED and product is not None:
        ld.notify_seller(product, "Upload your listing documents",
                         f"A buyer is waiting: upload your vehicle documents from My Listings, Edit "
                         f"listing, by {due}, or the deal is released.")
        outbox.push(seller.id, f"Upload your listing documents \u00b7 {title}",
                    "Upload your vehicle documents from Edit listing so the deal can lock.",
                    deal.product_id, deal.buyer_id)


def notify_doc_reviewers(deal, title, body):
    """The people who may check documents: superadmins, plus the deal's
    lender's own people when one is set."""
    from utils import is_superadmin
    for uid in staff_user_ids("deals.view"):
        u = User.query.get(uid)
        if u is not None and is_superadmin(u):
            Notification.push(uid, "system", title[:200], body=body,
                              link_page="admin", link_ref=deal.product_id)
    if deal.financer_id:
        notify_lender(deal.financer_id, title, body, deal.product_id)


def lender_member_ids(financer_id):
    out = []
    for m in FinancerMember.query.filter_by(financer_id=financer_id).all():
        u = User.query.get(m.user_id)
        if u and (u.account_status or "active") == "active":
            out.append(u.id)
    return out


def notify_lender(financer_id, title, body, product_id=None):
    """Bell notification for the lender's own people. Opens the admin panel
    (their Lender portal tab) on the website."""
    from permissions import has_capability
    for uid in lender_member_ids(financer_id):
        u = User.query.get(uid)
        if u and has_capability(u, "lender.portal"):
            Notification.push(uid, "system", title[:200], body=body,
                              link_page="admin", link_ref=product_id)


# --------------------------------------------------------------------------
# Lender-side steps, shared by staff (deals/routes.py) and the lender portal
# --------------------------------------------------------------------------

def mark_ready_to_pay(deal, actor, source, note, outbox):
    """The lender has sanctioned the loan, the agreement and auto-debit are
    signed, and (for vehicles) the vehicle and documents are checked.
    Returns an error string or None."""
    if deal.status != DEAL_LOAN_IN_PROGRESS:
        return f"Only a deal in Loan in progress can be marked Ready to pay (this one is {deal.status})."
    app = linked_application(deal)
    if not app or app.status != "approved":
        return "Approve the buyer's loan application first."
    if not app.financer_id:
        return "Set which lender is funding the loan on the EMI application first."

    now = utcnow()
    deal.status = DEAL_READY_TO_PAY
    deal.ready_to_pay_at = now
    deal.financer_id = app.financer_id
    if not deal.handover_code:
        deal.handover_code = new_handover_code()
        deal.handover_code_attempts = 0
    add_event(deal, "ready_to_pay", actor.id if actor else None, note=note, by=source)
    add_loan_event(app, "ready_to_pay", source, actor.id if actor else None, note)

    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    lender = financer_name(app.financer_id)
    item = "vehicle" if deal.loan_type == LOAN_VEHICLE else "item"
    text = (f"{lender} has approved the loan and is ready to pay. Meet to hand over the "
            f"{item}: the buyer pays the down payment, both tap Ready, and the lender pays "
            "the seller.")
    post_thread_message(deal.product_id, deal.seller_id, deal.buyer_id, text, "system")
    for uid, other in ((deal.buyer_id, deal.seller_id), (deal.seller_id, deal.buyer_id)):
        notify(uid, f"Ready to pay \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(uid, f"Ready to pay \u00b7 {title}", text, deal.product_id, other)
    return None


def clean_utr(raw):
    utr = "".join(str(raw or "").split()).upper()
    return utr if (6 <= len(utr) <= 30 and utr.isalnum()) else None


def mark_paid(deal, actor, source, utr, outbox):
    """The lender paid the seller. Returns an error string or None."""
    if deal.status != DEAL_READY_TO_PAY:
        return f"Only a Ready to pay deal can be marked Paid (this one is {deal.status})."
    if not (deal.buyer_ready_at and deal.seller_ready_at):
        return "Both buyer and seller must tap Ready at the handover before the payout."
    now = utcnow()
    deal.status = DEAL_PAID
    deal.paid_at = now
    deal.utr = utr
    add_event(deal, "paid", actor.id if actor else None, utr=utr, by=source)
    app = linked_application(deal)
    amount_text = ""
    if app:
        app.status = "disbursed"
        app.disbursed_at = app.disbursed_at or now
        add_loan_event(app, "disbursed", source, actor.id if actor else None, f"UTR {utr}")
        amount_text = f" {inr(app.loan_amount)}"

    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    lender = financer_name(deal.financer_id) or "The lender"
    item = "vehicle" if deal.loan_type == LOAN_VEHICLE else "item"
    text = (f"{lender} has paid{amount_text} to the seller. UTR {utr}. Seller: check your "
            f"bank, then hand over the {item}. The buyer gives you their handover code; "
            "enter it in the deal to finish.")
    post_thread_message(deal.product_id, deal.buyer_id, deal.seller_id, text, "system")
    for uid, other in ((deal.buyer_id, deal.seller_id), (deal.seller_id, deal.buyer_id)):
        notify(uid, f"Paid \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(uid, f"Paid \u00b7 {title}", text, deal.product_id, other)
    return None


# --------------------------------------------------------------------------
# Loans and completion (phase 2 and 3, manual mode)
# --------------------------------------------------------------------------

def linked_application(deal):
    """The deal's current loan application: the newest one linked to it."""
    return (EmiApplication.query.filter_by(deal_id=deal.id)
            .order_by(EmiApplication.applied_at.desc()).first())


def add_loan_event(app, status, source, actor_id, note=None, provider_event_id=None):
    ev = LoanStatusEvent(application_id=app.id, deal_id=app.deal_id, status=status,
                         source=source, actor_id=actor_id,
                         note=(note or "")[:500] or None,
                         provider_event_id=provider_event_id)
    db.session.add(ev)
    return ev


def financer_name(financer_id):
    if not financer_id:
        return None
    f = Financer.query.get(financer_id)
    return f.name if f else None


def loan_summary(deal, viewer_id, is_staff=False):
    """What each side sees about the loan. The buyer (and staff) see the
    terms; the seller sees only whether it is moving and who the lender is,
    because the buyer's loan amount and EMI are the buyer's business."""
    app = linked_application(deal)
    if not app:
        return None
    out = {"status": app.status, "lender": financer_name(app.financer_id),
           "applied_at": app.to_dict()["applied_at"]}
    if is_staff or viewer_id == deal.buyer_id:
        out.update({
            "application_id": app.id,
            "loan_amount": float(app.loan_amount),
            "down_payment": float(app.down_payment),
            "tenure_months": app.tenure_months,
            "interest_rate": float(app.interest_rate),
            "monthly_emi": float(app.monthly_emi),
            "nbfc_application_reference": app.nbfc_application_reference,
            "kfs_url": app.kfs_url,
        })
    return out


def staff_user_ids(capability="deals.manage"):
    """Users who can act on the DealLock staff queue."""
    from models import Admin
    from permissions import has_capability
    ids = {row.user_id for row in Admin.query.filter_by(is_active=True).all()}
    ids |= {u.id for u in User.query.filter_by(is_admin=True).all()}
    out = []
    for uid in ids:
        u = User.query.get(uid)
        if u and (u.account_status or "active") == "active" and has_capability(u, capability):
            out.append(uid)
    return out


def notify_staff(title, body, product_id=None):
    """Bell notification for whoever handles the DealLock queue. Opens the
    admin panel on the website."""
    for uid in staff_user_ids():
        Notification.push(uid, "system", title[:200], body=body,
                          link_page="admin", link_ref=product_id)


def complete_deal(deal, actor_id, outbox):
    """The handover is confirmed: the deal is done, the listing is sold, and
    a purchase record is written the same way "Mark sold" does, so it shows
    in sales history and the admin dashboard."""
    now = utcnow()
    deal.status = DEAL_COMPLETED
    deal.completed_at = now

    product = Product.query.get(deal.product_id)
    if product is not None:
        product.status = PRODUCT_SOLD

    app = linked_application(deal)
    purchase = Purchase(
        product_id=deal.product_id,
        financer_id=deal.financer_id,
        emi_application_id=app.id if app else None,
        total_amount=deal.agreed_price,
        payment_method="emi" if app else "full_payment",
        status="completed",
    )
    db.session.add(purchase)
    db.session.flush()
    db.session.add(PurchaseUser(purchase_id=purchase.purchase_id, user_id=deal.seller_id,
                                role_in_purchase="seller"))
    db.session.add(PurchaseUser(purchase_id=purchase.purchase_id, user_id=deal.buyer_id,
                                role_in_purchase="buyer"))

    # A finished deal shows both sides follow through: their cancels-in-a-row
    # count starts again.
    for uid in (deal.buyer_id, deal.seller_id):
        u = User.query.get(uid)
        if u is not None:
            u.consecutive_cancels = 0

    add_event(deal, "completed", actor_id, purchase_id=purchase.purchase_id)
    title = product.title if product else "the listing"
    text = "Handover confirmed. The deal is complete."
    if actor_id == deal.seller_id:
        sender, receiver = deal.seller_id, deal.buyer_id
    else:
        sender, receiver = deal.buyer_id, deal.seller_id
    post_thread_message(deal.product_id, sender, receiver, text, "system")
    for uid, other in ((deal.buyer_id, deal.seller_id), (deal.seller_id, deal.buyer_id)):
        if uid == actor_id:
            continue
        notify(uid, f"Deal completed \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(uid, f"Deal completed \u00b7 {title}", text, deal.product_id, other)
    return purchase


def apply_loan_status_to_deal(app, old_status, new_status, staff, outbox, source="staff"):
    """Staff changed a deal-linked EMI application's status in the admin
    panel. Called after the new status is set, before commit. Returns an
    error string (caller rolls back and answers 409) or None.

        approved           the deal stays in Loan in progress; the seller is
                           told. Ready to pay is its own step, because a
                           sanction isn't the same as the lender being ready
                           to pay at a handover.
        rejected/cancelled releases the deal (design: "loan rejected"), not
                           counted against anyone. The listing goes back on sale.
        disbursed          refused: use "Mark paid" on the deal, which also
                           records the UTR both sides need to see.
    Once the deal is Ready to pay or later, only a rejection/cancellation
    (before Paid) or "completed" (after Paid) can still be set here."""
    from models.deal import (DEAL_DISPUTED, DEAL_PAID, DEAL_READY_TO_PAY)
    if not app.deal_id or new_status == old_status:
        return None
    deal = Deal.query.filter_by(id=app.deal_id).with_for_update().first()
    if deal is None:
        return None
    current = linked_application(deal)
    if current is not None and current.id != app.id:
        add_loan_event(app, new_status, source, staff.id if staff else None)
        return None   # an older, superseded application; no effect on the deal

    if new_status == "disbursed" and deal.status not in (DEAL_PAID, DEAL_DISPUTED, DEAL_COMPLETED):
        return ("This loan belongs to a DealLock deal. Use Mark paid on the deal instead; "
                "it records the UTR the buyer and seller need to see.")
    if deal.status in (DEAL_PAID, DEAL_DISPUTED, DEAL_COMPLETED) and new_status not in ("completed", "disbursed"):
        return "The lender has already paid out on this deal, so the loan can only be marked completed now."
    if deal.status == DEAL_READY_TO_PAY and new_status not in ("rejected", "cancelled"):
        return ("This deal is already Ready to pay. Only rejecting or cancelling the loan "
                "(which releases the deal) is possible here.")

    add_loan_event(app, new_status, source, staff.id if staff else None, app.decision_notes)

    if new_status in ("rejected", "cancelled") and deal.status in RELEASABLE_STATES:
        reason = RELEASE_LOAN_REJECTED if new_status == "rejected" else RELEASE_STAFF_ACTION
        release_deal(deal, reason, staff.id if staff else None, outbox,
                     note=(app.decision_notes or "")[:255] or None)
    elif new_status == "approved" and deal.status == DEAL_LOAN_IN_PROGRESS:
        add_event(deal, "loan_approved", staff.id if staff else None,
                  lender=financer_name(app.financer_id))
        product = Product.query.get(deal.product_id)
        title = product.title if product else "the listing"
        if deal.loan_type == LOAN_VEHICLE:
            text = ("The buyer's loan is approved. Next, the lender checks the vehicle and "
                    "documents and marks the deal Ready to pay.")
        else:
            text = "The buyer's loan is approved. Next, the lender marks the deal Ready to pay."
        post_thread_message(deal.product_id, deal.buyer_id, deal.seller_id, text, "system")
        notify(deal.seller_id, f"Loan approved \u00b7 {title}"[:200], text, deal.product_id)
        outbox.push(deal.seller_id, f"Loan approved \u00b7 {title}", text, deal.product_id, deal.buyer_id)
    return None


# --------------------------------------------------------------------------
# Timer job
# --------------------------------------------------------------------------

def expire_offers(now, batch=200):
    """Open offers past their 48 hours become 'expired'. No message or push:
    the card in the chat shows Expired on its own, and a buzz for every
    lapsed offer would be noise."""
    offers = (DealOffer.query
              .filter(DealOffer.status == OFFER_OPEN, DealOffer.expires_at <= now)
              .order_by(DealOffer.expires_at)
              .limit(batch)
              .with_for_update(skip_locked=True)
              .all())
    for offer in offers:
        offer.status = OFFER_EXPIRED
        offer.responded_at = now
    return len(offers)


def release_unconfirmed(now, outbox, batch=100):
    """Deals not confirmed by confirm_by are released: the buyer didn't
    accept the warning in time, or (after the buyer did) the seller didn't
    confirm. Not counted against anyone: not answering is how either side
    says no before the deal is confirmed."""
    deals = (Deal.query
             .filter(Deal.status == DEAL_AWAITING_SELLER, Deal.confirm_by <= now)
             .order_by(Deal.confirm_by)
             .limit(batch)
             .with_for_update(skip_locked=True)
             .all())
    count = 0
    for deal in deals:
        reason = RELEASE_BUYER_NO_RESPONSE if deal.buyer_ack_at is None else RELEASE_SELLER_NO_RESPONSE
        if release_deal(deal, reason, None, outbox):
            count += 1
    return count


def purge_closed_deal_documents(now, batch=200):
    """Deletes document files DOC_RETENTION_DAYS after a deal closed. The
    rows stay (who checked what, and when); only the files go."""
    cutoff = now - timedelta(days=rules.DOC_RETENTION_DAYS)
    from . import doc_storage
    rows = (DealDocument.query.join(Deal, Deal.id == DealDocument.deal_id)
            .filter(DealDocument.deleted_at.is_(None),
                    DealDocument.storage_key.isnot(None),
                    db.or_(db.and_(Deal.status == DEAL_COMPLETED, Deal.completed_at <= cutoff),
                           db.and_(Deal.status == DEAL_RELEASED, Deal.released_at <= cutoff)))
            .limit(batch).all())
    from .listing_docs import key_in_use_elsewhere
    for doc in rows:
        key = doc.storage_key
        doc.storage_key = None
        doc.deleted_at = now
        # A seller file copied to its listing (phase 5) stays for the listing.
        if not key_in_use_elsewhere(key, deal_doc_id=doc.id):
            doc_storage.delete(key)
    return len(rows)


def release_expired_reservations(now, outbox, batch=100):
    """Deals still before Ready to pay when the reservation runs out are
    released, and the listing goes back on sale. That includes deals whose
    documents weren't approved in time."""
    deals = (Deal.query
             .filter(Deal.status.in_(TIMED_STATES), Deal.reserved_until <= now)
             .order_by(Deal.reserved_until)
             .limit(batch)
             .with_for_update(skip_locked=True)
             .all())
    count = 0
    for deal in deals:
        if release_deal(deal, RELEASE_RESERVATION_EXPIRED, None, outbox):
            count += 1
    return count


def flag_stalled_loans(now):
    """Loans sitting in Loan in progress for STALLED_LOAN_HOURS with no
    status change get one staff notification a day, so nothing is
    forgotten while lenders update us by phone or email."""
    from datetime import timedelta
    cutoff = now - timedelta(hours=STALLED_LOAN_HOURS)
    day_ago = now - timedelta(hours=24)
    flagged = 0
    deals = Deal.query.filter(Deal.status == DEAL_LOAN_IN_PROGRESS,
                              Deal.loan_applied_at <= cutoff).all()
    for deal in deals:
        last_change = (db.session.query(db.func.max(LoanStatusEvent.created_at))
                       .filter(LoanStatusEvent.deal_id == deal.id).scalar())
        if last_change and last_change > cutoff:
            continue
        recent_flag = DealEvent.query.filter(DealEvent.deal_id == deal.id,
                                             DealEvent.event_type == "loan_stalled",
                                             DealEvent.created_at > day_ago).first()
        if recent_flag:
            continue
        add_event(deal, "loan_stalled", None, hours=STALLED_LOAN_HOURS)
        product = Product.query.get(deal.product_id)
        notify_staff(f"Loan stalled \u00b7 {product.title if product else 'deal'}",
                     f"No loan update for {STALLED_LOAN_HOURS} hours. Reservation ends "
                     f"{short_date(deal.reserved_until)}.", deal.product_id)
        flagged += 1
    return flagged
