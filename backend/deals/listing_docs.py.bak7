"""
DealLock phase 5: the seller's documents belong to the LISTING.

A car or bike listing needs the vehicle documents (RC, insurance, PUC, PAN,
masked Aadhaar, bank proof, plus the loan NOC when the vehicle ever had a
loan). The seller uploads them when posting, or later from Edit listing.
They are checked once, by a superadmin or by a lender (NBFC) that funds
vehicle loans, and every deal on the listing uses them. In a deal only the
buyer uploads (PAN, masked Aadhaar).

Who sees what
  the seller (owner)   the checklist with statuses and reasons, never a file
  superadmin           everything, and checks them
  a lender's people    listings of the kinds of loans their lender funds
                       (vehicle loans for cars and bikes), and checks them
  anyone else          nothing (404)

States of a listing's documents (summary["state"])
  none       the listing needs no documents (not a vehicle)
  needed     something is missing or was sent back, or the loan question
             is unanswered
  checking   everything is uploaded, at least one file waits for a check
  verified   every needed document is approved

Once a deal on the listing is LOCKED (or later), the documents are frozen:
they are what the lender is lending against.

Nothing here commits. Callers commit once.
"""
import logging
from datetime import timedelta

from extensions import db
from models import Deal, DealDocument, Financer, ListingDocument, Notification, Product, User
from models.deal import (DEAL_DISPUTED, DEAL_DOCS_PENDING, DEAL_LOAN_IN_PROGRESS, DEAL_LOCKED,
                         DEAL_PAID, DEAL_READY_TO_PAY, DOC_APPROVED, DOC_REJECTED, DOC_UPLOADED,
                         LIVE_DEAL_STATES, LOAN_PERSONAL, LOAN_VEHICLE)
from . import rules

log = logging.getLogger("dealbridge.deals.listing_docs")

# A deal in one of these has locked the listing's documents.
FROZEN_DEAL_STATES = (DEAL_LOCKED, DEAL_LOAN_IN_PROGRESS, DEAL_READY_TO_PAY, DEAL_PAID, DEAL_DISPUTED)
STATE_NONE, STATE_NEEDED, STATE_CHECKING, STATE_VERIFIED = "none", "needed", "checking", "verified"


# --------------------------------------------------------------------------
# What a listing needs
# --------------------------------------------------------------------------

def loan_type_of(product):
    return LOAN_VEHICLE if rules.is_vehicle_category(product.category) else LOAN_PERSONAL


def needs_docs(product):
    return bool(product) and (rules.is_vehicle_category(product.category)
                              or bool(rules.listing_required_docs(product.category)))


def question_pending(product):
    return rules.is_vehicle_category(product.category) and product.vehicle_had_loan is None


def required(product):
    return rules.listing_required_docs(product.category, product.vehicle_had_loan)


def documents(product_id):
    return {d.doc_type: d for d in ListingDocument.query.filter_by(product_id=product_id).all()}


def checklist(product, have=None):
    """[{type, label, hint, party, source, required, status, document}] in
    display order. Includes documents uploaded but no longer needed (a NOC
    after the answer changed to no), marked required False."""
    have = documents(product.id) if have is None else have
    need = required(product)
    out = []
    for doc_type in rules.SELLER_DOC_TYPES:
        doc = have.get(doc_type)
        if doc_type not in need and doc is None:
            continue
        meta = rules.DOC_TYPES[doc_type]
        out.append({
            "type": doc_type, "label": meta["label"], "hint": meta["hint"],
            "party": "seller", "source": "listing",
            "required": doc_type in need,
            "status": doc.effective_status if doc else "missing",
            "document": doc.to_dict() if doc else None,
        })
    return out


def summary(product, have=None):
    if not needs_docs(product):
        return {"state": STATE_NONE, "needed": 0, "uploaded": 0, "approved": 0,
                "to_check": 0, "rejected": 0, "question_pending": False}
    rows = [r for r in checklist(product, have) if r["required"]]
    st = [r["status"] for r in rows]
    s = {
        "needed": len(rows),
        "uploaded": sum(1 for x in st if x in (DOC_UPLOADED, DOC_APPROVED)),
        "approved": sum(1 for x in st if x == DOC_APPROVED),
        "to_check": sum(1 for x in st if x == DOC_UPLOADED),
        "rejected": sum(1 for x in st if x == DOC_REJECTED),
        "question_pending": question_pending(product),
    }
    if s["question_pending"] or s["uploaded"] < s["needed"]:
        s["state"] = STATE_NEEDED
    elif s["approved"] == s["needed"]:
        s["state"] = STATE_VERIFIED
    else:
        s["state"] = STATE_CHECKING
    return s


def state(product):
    return summary(product)["state"]


def all_uploaded(product):
    """The seller has done their part (everything in, maybe not checked)."""
    return state(product) in (STATE_NONE, STATE_CHECKING, STATE_VERIFIED)


def verified(product):
    return state(product) in (STATE_NONE, STATE_VERIFIED)


def states_for(products):
    """{product_id: state} for many listings, in one query."""
    ids = [p.id for p in products if needs_docs(p)]
    by = {}
    if ids:
        for d in ListingDocument.query.filter(ListingDocument.product_id.in_(ids)).all():
            by.setdefault(d.product_id, {})[d.doc_type] = d
    return {p.id: (summary(p, by.get(p.id, {}))["state"] if needs_docs(p) else STATE_NONE)
            for p in products}


def live_deal(product_id, lock=False):
    q = Deal.query.filter(Deal.product_id == product_id, Deal.status.in_(LIVE_DEAL_STATES))
    if lock:
        q = q.with_for_update()
    return q.first()


def frozen_by(product_id):
    """The deal that has locked these documents, or None."""
    d = live_deal(product_id)
    return d if d is not None and d.status in FROZEN_DEAL_STATES else None


# --------------------------------------------------------------------------
# Who may see and check them
# --------------------------------------------------------------------------

def _lender_fid(user):
    from .service import lender_financer_id
    return lender_financer_id(user)


def lender_funds(fid, loan_type):
    f = Financer.query.get(fid) if fid else None
    return f is not None and (f.status or "active") == "active" and loan_type in f.loan_type_list()


def can_review(user, product):
    """A superadmin, or the people of a lender that funds this kind of loan
    (or that is the lender on a live deal for this listing)."""
    if not user or not product:
        return False
    from utils import is_superadmin
    if is_superadmin(user):
        return True
    fid = _lender_fid(user)
    if fid is None:
        return False
    if lender_funds(fid, loan_type_of(product)):
        return True
    d = live_deal(product.id)
    return d is not None and d.financer_id == fid


def can_view(user, product):
    return bool(user and product and (user.id == product.posted_by or can_review(user, product)))


def view(product, user):
    have = documents(product.id)
    frozen = frozen_by(product.id)
    deal = live_deal(product.id)
    owner = user is not None and user.id == product.posted_by
    return {
        "product_id": product.id,
        "title": product.title,
        "category": product.category,
        "needs_docs": needs_docs(product),
        "vehicle_had_loan": product.vehicle_had_loan,
        "documents": checklist(product, have),
        "summary": summary(product, have),
        "frozen": frozen is not None,
        "deal_status": deal.status if deal else None,
        "can_upload": owner and needs_docs(product) and frozen is None,
        "can_review": can_review(user, product),
        "can_view_files": can_review(user, product),
    }


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def reviewer_ids(product):
    """Superadmins, plus the people of every active lender that funds this
    kind of loan."""
    from utils import is_superadmin
    from .service import lender_member_ids, staff_user_ids
    ids = []
    for uid in staff_user_ids("deals.view"):
        u = User.query.get(uid)
        if u is not None and is_superadmin(u):
            ids.append(uid)
    lt = loan_type_of(product)
    for f in Financer.query.filter_by(status="active").all():
        if lt in f.loan_type_list():
            for uid in lender_member_ids(f.financer_id):
                if uid not in ids:
                    ids.append(uid)
    return ids


def notify_reviewers(product, body):
    """kind listing_docs + the listing id: the website opens that listing's
    documents in the admin panel."""
    for uid in reviewer_ids(product):
        Notification.push(uid, "listing_docs", f"Listing documents to check · {product.title}"[:200],
                          body=body, link_page="admin", link_ref=product.id)


def notify_seller(product, title, body):
    if product.posted_by:
        Notification.push(product.posted_by, "listing", f"{title} · {product.title}"[:200],
                          body=body, link_page="mylistings", link_ref=product.id)


# --------------------------------------------------------------------------
# Changes
# --------------------------------------------------------------------------

def key_in_use_elsewhere(key, listing_doc_id=None, deal_doc_id=None):
    """A file can be shared by a listing document and a deal document (the
    migration copied seller documents from live deals to their listing).
    Such a file is deleted only once neither row needs it."""
    if not key:
        return False
    q1 = ListingDocument.query.filter(ListingDocument.storage_key == key,
                                      ListingDocument.deleted_at.is_(None))
    if listing_doc_id:
        q1 = q1.filter(ListingDocument.id != listing_doc_id)
    q2 = DealDocument.query.filter(DealDocument.storage_key == key, DealDocument.deleted_at.is_(None))
    if deal_doc_id:
        q2 = q2.filter(DealDocument.id != deal_doc_id)
    return q1.first() is not None or q2.first() is not None


def after_change(product, before_state, actor_id, outbox, replaced_label=None):
    """Everything that follows a change to a listing's documents: reviewers
    told when it becomes ready to check, the seller told when verified, and
    a deal waiting for these documents moved on (and locked if the buyer's
    side is verified too)."""
    from .service import add_event, docs_all_approved, lock_deal, notify as deal_notify
    now_state = state(product)
    if now_state == STATE_CHECKING and before_state != STATE_CHECKING:
        if replaced_label:
            body = f"{replaced_label} on this listing was replaced. Check it again."
        else:
            body = "The seller has uploaded every document for this listing. Check and approve them."
        notify_reviewers(product, body)
    if now_state == STATE_VERIFIED and before_state != STATE_VERIFIED:
        notify_seller(product, "Documents verified",
                      "Your listing's documents are verified. Deals on it can lock as soon as the "
                      "buyer's documents are verified too.")

    deal = live_deal(product.id, lock=True)
    if deal is not None and deal.status == DEAL_DOCS_PENDING:
        if now_state in (STATE_CHECKING, STATE_VERIFIED) and before_state == STATE_NEEDED:
            text = ("The seller has uploaded their vehicle documents. They're being checked by "
                    "DealBriz or the lender.")
            deal_notify(deal.buyer_id, f"The seller uploaded their documents · {product.title}"[:200],
                        text, product.id)
        if now_state == STATE_VERIFIED and docs_all_approved(deal):
            lock_deal(deal, actor_id, outbox)
    return now_state


def record_deal_event(product, event_type, actor_id, **data):
    """Mirror a listing document change on the timeline of a deal waiting
    for it, so both sides see it where they look."""
    from .service import add_event
    deal = live_deal(product.id)
    if deal is not None and deal.status == DEAL_DOCS_PENDING:
        add_event(deal, event_type, actor_id, source="listing", **data)


# --------------------------------------------------------------------------
# Timer job
# --------------------------------------------------------------------------

def purge_listing_documents(now, batch=200):
    """Files of listings that are no longer on sale (sold or removed) are
    deleted rules.DOC_RETENTION_DAYS after they were uploaded, once no live
    deal uses them and no deal on the listing closed within that time. The
    rows stay (what was checked, by whom); only the files go. A file still
    used by a deal document is kept."""
    cutoff = now - timedelta(days=rules.DOC_RETENTION_DAYS)
    rows = (ListingDocument.query.join(Product, Product.id == ListingDocument.product_id)
            .filter(ListingDocument.deleted_at.is_(None),
                    ListingDocument.storage_key.isnot(None),
                    ListingDocument.uploaded_at <= cutoff,
                    Product.status.in_(("sold", "removed")))
            .limit(batch).all())
    purged = 0
    checked = {}
    for doc in rows:
        pid = doc.product_id
        if pid not in checked:
            recent = Deal.query.filter(
                Deal.product_id == pid,
                db.or_(Deal.status.in_(LIVE_DEAL_STATES),
                       Deal.completed_at > cutoff, Deal.released_at > cutoff)).first()
            checked[pid] = recent is None
        if not checked[pid]:
            continue
        key = doc.storage_key
        doc.storage_key = None
        doc.deleted_at = now
        if not key_in_use_elsewhere(key, listing_doc_id=doc.id):
            from . import doc_storage
            doc_storage.delete(key)
        purged += 1
    return purged
