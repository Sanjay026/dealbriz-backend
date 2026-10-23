"""
DealLock phase 5: a listing's documents (the seller's, checked once).

    GET  /api/listing-docs/queue?state=checking|needed|verified|all
                                                superadmin / lender: listings to check
    GET  /api/listing-docs/<pid>                checklist (owner, superadmin, lender)
    POST /api/listing-docs/<pid>/loan-answer    owner: {had_loan: true|false}
    POST /api/listing-docs/<pid>/<type>         owner uploads (multipart "file")
    GET  /api/listing-docs/<pid>/files/<doc>    open one (superadmin, lender only)
    POST /api/listing-docs/<pid>/review/<doc>   superadmin / lender: {decision:
                                                approve | reject, reason (reject)}

The rules (who may see what, states, frozen once a deal locks) are in
deals/listing_docs.py.
"""
import logging

from flask import Blueprint, jsonify, redirect, request, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import ListingDocument, Product, User
from models.deal import DOC_APPROVED, DOC_REJECTED, DOC_UPLOADED
from utils import current_user, is_superadmin
from deals import doc_storage, listing_docs as ld, rules
from deals.service import Outbox, display_name, lender_financer_id, utcnow

log = logging.getLogger("dealbridge.listing_docs")

bp = Blueprint("listing_docs", __name__, url_prefix="/api/listing-docs")

RACE_MESSAGE = "Something changed while you were doing that. Please refresh and try again."
FROZEN_MESSAGE = ("A deal on this listing is locked, so its documents can't be changed now. "
                  "They are what the lender is lending against.")


def _err(message, code):
    return jsonify({"error": message}), code


def _me():
    user = current_user()
    if not user or (user.account_status or "active") != "active":
        return None
    return user


def _product(pid, lock=False):
    q = Product.query.filter_by(id=pid)
    if lock:
        q = q.with_for_update()
    return q.first()


def _commit():
    try:
        db.session.commit()
        return None
    except IntegrityError:
        db.session.rollback()
        return _err(RACE_MESSAGE, 409)


# --------------------------------------------------------------------------
# Queue (superadmin and lenders)
# --------------------------------------------------------------------------

@bp.route("/queue", methods=["GET"])
@jwt_required()
def queue():
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    superadmin = is_superadmin(user)
    fid = None if superadmin else lender_financer_id(user)
    if not superadmin and fid is None:
        return _err("Only a superadmin or a lender can check listing documents.", 403)
    want = (request.args.get("state") or "checking").lower()
    if want not in ("checking", "needed", "verified", "all"):
        return _err("state must be checking, needed, verified or all.", 400)

    q = Product.query.filter(Product.status.in_(("active", "reserved")))
    products = [p for p in q.all() if ld.needs_docs(p)]
    if not superadmin:
        products = [p for p in products if ld.can_review(user, p)]
    by = {}
    if products:
        for d in ListingDocument.query.filter(ListingDocument.product_id.in_([p.id for p in products])).all():
            by.setdefault(d.product_id, {})[d.doc_type] = d
    sellers = {u.id: u for u in User.query.filter(User.id.in_({p.posted_by for p in products if p.posted_by})).all()} \
        if products else {}
    rows = []
    for p in products:
        have = by.get(p.id, {})
        s = ld.summary(p, have)
        if want != "all" and s["state"] != want:
            continue
        uploaded = [d.uploaded_at for d in have.values() if d.status == DOC_UPLOADED and d.uploaded_at]
        seller = sellers.get(p.posted_by)
        deal = ld.live_deal(p.id)
        rows.append({
            "product": {"id": p.id, "title": p.title, "category": p.category, "price": float(p.price),
                        "image_url": p.image_url, "location": p.location, "city": p.city, "status": p.status},
            "seller": {"id": seller.id, "name": display_name(seller), "phone": seller.phone} if seller else None,
            "summary": s,
            "vehicle_had_loan": p.vehicle_had_loan,
            "deal_status": deal.status if deal else None,
            "waiting_since": (min(uploaded).isoformat() + "Z") if uploaded else None,
        })
    # Oldest waiting first; a deal waiting on the documents goes to the top.
    rows.sort(key=lambda r: (r["deal_status"] != "docs_pending", r["waiting_since"] or "9"))
    return jsonify({"state": want, "rows": rows})


# --------------------------------------------------------------------------
# One listing
# --------------------------------------------------------------------------

@bp.route("/<pid>", methods=["GET"])
@jwt_required()
def get_listing_docs(pid):
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = _product(pid)
    if not product or not ld.can_view(user, product):
        return _err("Not found.", 404)
    return jsonify(ld.view(product, user))


@bp.route("/<pid>/loan-answer", methods=["POST"])
@jwt_required()
def loan_answer(pid):
    """{had_loan: true|false}: has this vehicle ever had a loan on it (a
    bank's name on the RC)? Yes adds the old lender's NOC to the list."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = _product(pid, lock=True)
    if not product or user.id != product.posted_by:
        return _err("Not found.", 404)
    if not rules.is_vehicle_category(product.category):
        return _err("This question is only for cars and bikes.", 409)
    if ld.frozen_by(product.id):
        return _err(FROZEN_MESSAGE, 409)
    had = (request.get_json(silent=True) or {}).get("had_loan")
    if not isinstance(had, bool):
        return _err("Answer yes or no.", 400)
    if product.vehicle_had_loan != had:
        before = ld.state(product)
        product.vehicle_had_loan = had
        db.session.flush()
        ld.record_deal_event(product, "loan_answer", user.id, had_loan=had)
        deal = ld.live_deal(product.id)
        if deal is not None:
            deal.vehicle_had_loan = had   # kept in step for the deal's own record
        outbox = Outbox()
        ld.after_change(product, before, user.id, outbox)
        failed = _commit()
        if failed:
            return failed
        outbox.send()
    return jsonify(ld.view(product, user))


@bp.route("/<pid>/<doc_type>", methods=["POST"])
@jwt_required()
def upload(pid, doc_type):
    """Multipart "file" (JPG, PNG, WEBP or PDF, up to 8 MB). Uploading again
    replaces the earlier file of that type, even a verified one, which then
    needs checking again."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = _product(pid, lock=True)
    if not product or user.id != product.posted_by:
        return _err("Not found.", 404)
    if doc_type not in rules.SELLER_DOC_TYPES:
        return _err("Unknown document type.", 404)
    if not ld.needs_docs(product):
        return _err("This listing doesn't need documents.", 409)
    if ld.frozen_by(product.id):
        return _err(FROZEN_MESSAGE, 409)
    if doc_type in rules.VEHICLE_LOAN_DOCS and ld.question_pending(product):
        return _err("First say whether the vehicle ever had a loan on it.", 409)
    if doc_type not in ld.required(product):
        return _err("That document isn't needed for this listing.", 409)

    ext, content_type, size, problem = doc_storage.check_upload(
        request.files.get("file"), rules.DOC_EXTENSIONS, rules.DOC_MAX_BYTES)
    if problem:
        return _err(problem, 400)

    doc = ListingDocument.query.filter_by(product_id=product.id, doc_type=doc_type).with_for_update().first()
    before = ld.state(product)
    replaced_verified = doc is not None and doc.status == DOC_APPROVED and doc.deleted_at is None
    file = request.files["file"]
    try:
        key = doc_storage.save(file, f"listings/{product.id}", doc_type, ext, content_type)
    except Exception:
        log.error("Listing document upload failed", exc_info=True)
        db.session.rollback()
        return _err("Could not save that file. Please try again.", 500)

    old_key = doc.storage_key if doc is not None else None
    old_id = doc.id if doc is not None else None
    now = utcnow()
    safe_name = "".join(ch for ch in file.filename if ch.isalnum() or ch in "._- ")[:200] or f"{doc_type}.{ext}"
    if doc is None:
        doc = ListingDocument(product_id=product.id, doc_type=doc_type)
        db.session.add(doc)
    doc.status = DOC_UPLOADED
    doc.storage_key = key
    doc.file_name = safe_name
    doc.content_type = content_type
    doc.size_bytes = size
    doc.uploaded_by = user.id
    doc.uploaded_at = now
    doc.reviewed_by = None
    doc.reviewed_financer_id = None
    doc.reviewed_at = None
    doc.reject_reason = None
    doc.deleted_at = None
    db.session.flush()
    ld.record_deal_event(product, "document_uploaded", user.id, doc_type=doc_type)

    outbox = Outbox()
    label = rules.DOC_TYPES[doc_type]["label"]
    ld.after_change(product, before, user.id, outbox, replaced_label=label if replaced_verified else None)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        doc_storage.delete(key)
        return _err(RACE_MESSAGE, 409)
    if old_key and old_key != key and not ld.key_in_use_elsewhere(old_key, listing_doc_id=old_id):
        doc_storage.delete(old_key)
    outbox.send()
    return jsonify(ld.view(product, user)), 201


@bp.route("/<pid>/files/<doc_id>", methods=["GET"])
@jwt_required()
def view_file(pid, doc_id):
    """A superadmin or a lender that may check this listing. Not the seller
    (they know what they uploaded) and not buyers: identity documents."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = _product(pid)
    if not product or not ld.can_review(user, product):
        return _err("Not found.", 404)
    doc = ListingDocument.query.filter_by(id=doc_id, product_id=product.id).first()
    if not doc:
        return _err("Not found.", 404)
    if not doc.storage_key:
        return _err("This file is no longer stored.", 410)
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


@bp.route("/<pid>/review/<doc_id>", methods=["POST"])
@jwt_required()
def review(pid, doc_id):
    """{decision: approve | reject, reason (needed to reject)}. The first
    decision counts: a document already checked can't be checked again
    until the seller uploads a new file."""
    user = _me()
    if not user:
        return _err("Please sign in again.", 401)
    product = _product(pid, lock=True)
    if not product or not ld.can_review(user, product):
        return _err("Not found.", 404)
    doc = ListingDocument.query.filter_by(id=doc_id, product_id=product.id).with_for_update().first()
    if not doc:
        return _err("Not found.", 404)
    data = request.get_json(silent=True) or {}
    decision = data.get("decision")
    reason = (data.get("reason") or "").strip()[:255]
    if decision not in ("approve", "reject"):
        return _err("decision must be approve or reject.", 400)
    if decision == "reject" and not reason:
        return _err("Say what is wrong, so the seller can fix it.", 400)
    if doc.effective_status != DOC_UPLOADED:
        return _err(f"This document is already {doc.effective_status}.", 409)

    before = ld.state(product)
    now = utcnow()
    superadmin = is_superadmin(user)
    doc.status = DOC_APPROVED if decision == "approve" else DOC_REJECTED
    doc.reviewed_by = user.id
    doc.reviewed_at = now
    doc.reviewed_financer_id = None if superadmin else lender_financer_id(user)
    doc.reject_reason = reason if decision == "reject" else None
    rejected_key = None
    if decision == "reject" and doc.storage_key:
        # A rejected file isn't kept: it may be exactly what must not be
        # stored (a full Aadhaar, someone else's PAN).
        rejected_key = doc.storage_key
        doc.storage_key = None
        doc.deleted_at = now
    db.session.flush()
    label = rules.DOC_TYPES.get(doc.doc_type, {}).get("label", doc.doc_type)
    ld.record_deal_event(product, "document_" + ("approved" if decision == "approve" else "rejected"),
                         user.id, doc_type=doc.doc_type, reason=doc.reject_reason,
                         by="staff" if superadmin else "lender")
    if decision == "reject":
        ld.notify_seller(product, "Document needs fixing",
                         f"{label} needs to be uploaded again: {reason}. Upload it from My Listings, "
                         "Edit listing.")
    outbox = Outbox()
    ld.after_change(product, before, user.id, outbox)
    failed = _commit()
    if failed:
        return failed
    if rejected_key and not ld.key_in_use_elsewhere(rejected_key, listing_doc_id=doc.id):
        doc_storage.delete(rejected_key)
    outbox.send()
    return jsonify(ld.view(product, user))
