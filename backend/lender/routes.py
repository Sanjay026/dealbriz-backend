"""
Lender portal: a lender's (NBFC's) own people work their own loans and
DealLock deals here, in the "Lender portal" tab of the admin panel.

Who can use it
  - users with the lender role (capability lender.portal) who are linked to
    one lender in financer_members. They only ever see that lender's work.
  - DealBriz staff with deals.manage, who pick a lender with ?financer_id=
    (to help, or to see what a lender sees).

    GET  /api/lender/me                        who am I working for
    GET  /api/lender/loans?status=             my lender's loan applications
    GET  /api/lender/loans/<id>                one, with its deal and timeline
    POST /api/lender/loans/<id>/status         {status: review|approved|rejected,
                                                decision_notes}
    POST /api/lender/loans/<id>/details        {nbfc_application_reference, kfs_url}
    GET  /api/lender/deals?status=             my lender's deals (incl. documents
                                               waiting for a check)
    POST /api/lender/deals/<id>/ready-to-pay   {note}
    POST /api/lender/deals/<id>/paid           {utr}

Seller documents are checked with the deal endpoints
(GET /api/deals/<id>/documents/<doc>/file and POST .../review), which let
the deal's own lender in.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from emi.status import set_emi_status
from models import Deal, EmiApplication, Financer, LoanStatusEvent, Product, User
from models.deal import DEAL_DOCS_PENDING, LIVE_DEAL_STATES
from permissions import has_capability
from utils import current_user
from deals import rules
from deals.service import (Outbox, apply_loan_status_to_deal, clean_utr,
                           display_name, docs_checklist, lender_financer_id,
                           mark_paid, mark_ready_to_pay)

bp = Blueprint("lender", __name__, url_prefix="/api/lender")

# What a lender may set on a loan from the portal. Disbursed comes from
# "Paid" on the deal (with the UTR); cancelled and completed stay with staff.
LENDER_LOAN_STATUSES = ("review", "approved", "rejected")
RACE_MESSAGE = "Something changed while you were doing that. Please refresh and try again."


def _err(message, code):
    return jsonify({"error": message}), code


def _scope():
    """(user, financer, error_response). A lender user gets their own
    lender; staff with deals.manage pass ?financer_id= (or financer_id in
    the body)."""
    user = current_user()
    if not user or (user.account_status or "active") != "active":
        return None, None, _err("Please sign in again.", 401)
    fid = lender_financer_id(user)
    if fid is not None:
        return user, Financer.query.get(fid), None
    if has_capability(user, "deals.manage"):
        raw = request.args.get("financer_id") or (request.get_json(silent=True) or {}).get("financer_id")
        if not raw:
            return user, None, _err("Choose a lender.", 400)
        financer = Financer.query.get(raw)
        if not financer:
            return user, None, _err("Unknown lender.", 404)
        return user, financer, None
    if has_capability(user, "lender.portal"):
        return user, None, _err("Your account isn't linked to a lender yet. "
                                "Ask a DealBriz superadmin to set it.", 403)
    return user, None, _err("You don't have permission to do that.", 403)


def _commit():
    try:
        db.session.commit()
        return None
    except IntegrityError:
        db.session.rollback()
        return _err(RACE_MESSAGE, 409)


def _product_brief(product):
    if not product:
        return None
    return {"id": product.id, "title": product.title, "category": product.category,
            "image_url": product.image_url, "city": product.city, "location": product.location}


def _deal_brief(deal):
    if not deal:
        return None
    d = {"id": deal.id, "status": deal.status, "loan_type": deal.loan_type,
         "agreed_price": float(deal.agreed_price),
         "reserved_until": deal.to_dict()["reserved_until"]}
    if deal.status == DEAL_DOCS_PENDING:
        need = [c for c in docs_checklist(deal) if c["required"]]
        d["docs_to_check"] = sum(1 for c in need if c["status"] == "uploaded")
        d["docs_needed"] = len(need)
    return d


def _loan_row(app, deals, products):
    d = app.to_dict()
    deal = deals.get(app.deal_id) if app.deal_id else None
    d["deal"] = _deal_brief(deal)
    d["product"] = _product_brief(products.get(app.product_id))
    return d


@bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user = current_user()
    if not user:
        return _err("Please sign in again.", 401)
    fid = lender_financer_id(user)
    financer = Financer.query.get(fid) if fid else None
    can_choose = fid is None and has_capability(user, "deals.manage")
    if not financer and not can_choose:
        if has_capability(user, "lender.portal"):
            return _err("Your account isn't linked to a lender yet. "
                        "Ask a DealBriz superadmin to set it.", 403)
        return _err("You don't have permission to do that.", 403)
    out = {"financer": financer.to_dict() if financer else None,
           "can_choose_financer": can_choose,
           "statuses": list(LENDER_LOAN_STATUSES),
           "doc_types": rules.public_rules()["doc_types"]}
    if can_choose:
        out["financers"] = [{"financer_id": f.financer_id, "name": f.name,
                             "loan_types": f.loan_type_list(), "status": f.status}
                            for f in Financer.query.order_by(Financer.name).all()]
    return jsonify(out)


@bp.route("/loans", methods=["GET"])
@jwt_required()
def list_loans():
    user, financer, problem = _scope()
    if problem:
        return problem
    q = EmiApplication.query.filter(EmiApplication.financer_id == financer.financer_id,
                                    EmiApplication.is_archived == False)  # noqa: E712
    status = (request.args.get("status") or "").strip()
    if status:
        q = q.filter(EmiApplication.status == status)
    apps = q.order_by(EmiApplication.applied_at.desc()).limit(500).all()
    deal_ids = {a.deal_id for a in apps if a.deal_id}
    product_ids = {a.product_id for a in apps if a.product_id}
    deals = {d.id: d for d in Deal.query.filter(Deal.id.in_(deal_ids)).all()} if deal_ids else {}
    products = ({p.id: p for p in Product.query.filter(Product.id.in_(product_ids)).all()}
                if product_ids else {})
    return jsonify([_loan_row(a, deals, products) for a in apps])


def _own_loan(app_id, financer, lock=False):
    q = EmiApplication.query.filter_by(id=app_id)
    if lock:
        q = q.with_for_update()
    app = q.first()
    if not app or app.financer_id != financer.financer_id:
        return None
    return app


@bp.route("/loans/<app_id>", methods=["GET"])
@jwt_required()
def get_loan(app_id):
    user, financer, problem = _scope()
    if problem:
        return problem
    app = _own_loan(app_id, financer)
    if not app:
        return _err("Loan not found.", 404)
    d = app.to_dict()
    applicant = User.query.get(app.user_id)
    d["applicant"] = ({"name": display_name(applicant), "email": applicant.email,
                       "phone": applicant.phone, "city": applicant.city} if applicant else None)
    d["product"] = _product_brief(Product.query.get(app.product_id)) if app.product_id else None
    d["events"] = [e.to_dict() for e in LoanStatusEvent.query.filter_by(application_id=app.id)
                   .order_by(LoanStatusEvent.created_at.asc()).all()]
    d["deal"] = None
    if app.deal_id:
        from deals.routes import deal_detail
        deal = Deal.query.get(app.deal_id)
        if deal and deal.financer_id == financer.financer_id:
            d["deal"] = deal_detail(deal, user)
        elif deal:
            d["deal"] = _deal_brief(deal)
    return jsonify(d)


@bp.route("/loans/<app_id>/status", methods=["POST"])
@jwt_required()
def set_loan_status(app_id):
    user, financer, problem = _scope()
    if problem:
        return problem
    app = _own_loan(app_id, financer, lock=True)
    if not app:
        return _err("Loan not found.", 404)
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in LENDER_LOAN_STATUSES:
        return _err("Status must be review, approved or rejected.", 400)
    if app.status in ("disbursed", "completed", "cancelled"):
        return _err(f"This loan is already {app.status}.", 409)
    notes = (data.get("decision_notes") or "").strip()[:2000]
    if new_status == "rejected" and not notes:
        return _err("Add a note saying why the loan isn't approved.", 400)
    if notes:
        app.decision_notes = notes

    old_status = set_emi_status(app, new_status, user)
    outbox = Outbox()
    if app.deal_id:
        problem_text = apply_loan_status_to_deal(app, old_status, new_status, user, outbox,
                                                 source="lender" if lender_financer_id(user) else "staff")
        if problem_text:
            db.session.rollback()
            return _err(problem_text, 409)
    failed = _commit()
    if failed:
        return failed
    outbox.send()
    return jsonify(app.to_dict())


@bp.route("/loans/<app_id>/details", methods=["POST"])
@jwt_required()
def set_loan_details(app_id):
    """The lender's own application reference and the Key Fact Statement
    link (https only)."""
    user, financer, problem = _scope()
    if problem:
        return problem
    app = _own_loan(app_id, financer, lock=True)
    if not app:
        return _err("Loan not found.", 404)
    data = request.get_json(silent=True) or {}
    if "nbfc_application_reference" in data:
        app.nbfc_application_reference = (str(data.get("nbfc_application_reference") or "")
                                          .strip()[:150] or None)
    if "kfs_url" in data:
        url = str(data.get("kfs_url") or "").strip()
        if url and not url.startswith("https://"):
            return _err("The KFS link must start with https://", 400)
        app.kfs_url = url[:500] or None
    failed = _commit()
    if failed:
        return failed
    return jsonify(app.to_dict())


@bp.route("/deals", methods=["GET"])
@jwt_required()
def list_deals():
    """?status=<one status>, or by default every live deal of this lender."""
    user, financer, problem = _scope()
    if problem:
        return problem
    q = Deal.query.filter(Deal.financer_id == financer.financer_id)
    status = (request.args.get("status") or "").strip()
    if status == "all":
        pass
    elif status:
        q = q.filter(Deal.status == status)
    else:
        q = q.filter(Deal.status.in_(LIVE_DEAL_STATES))
    deals = q.order_by(Deal.updated_at.desc()).limit(300).all()
    pids = {d.product_id for d in deals}
    uids = {d.buyer_id for d in deals} | {d.seller_id for d in deals}
    products = {p.id: p for p in Product.query.filter(Product.id.in_(pids)).all()} if pids else {}
    users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
    out = []
    for deal in deals:
        d = deal.to_dict()
        d.update(_deal_brief(deal))
        d["product"] = _product_brief(products.get(deal.product_id))
        for role in ("buyer", "seller"):
            u = users.get(getattr(deal, f"{role}_id"))
            d[role] = {"name": display_name(u), "phone": u.phone} if u else None
        out.append(d)
    return jsonify(out)


def _own_deal(deal_id, financer):
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or deal.financer_id != financer.financer_id:
        return None
    return deal


@bp.route("/deals/<deal_id>/ready-to-pay", methods=["POST"])
@jwt_required()
def ready_to_pay(deal_id):
    user, financer, problem = _scope()
    if problem:
        return problem
    deal = _own_deal(deal_id, financer)
    if not deal:
        return _err("Deal not found.", 404)
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()[:500] or None
    outbox = Outbox()
    problem_text = mark_ready_to_pay(deal, user, "lender" if lender_financer_id(user) else "staff",
                                     note, outbox)
    if problem_text:
        db.session.rollback()
        return _err(problem_text, 409)
    failed = _commit()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict())


@bp.route("/deals/<deal_id>/paid", methods=["POST"])
@jwt_required()
def paid(deal_id):
    user, financer, problem = _scope()
    if problem:
        return problem
    deal = _own_deal(deal_id, financer)
    if not deal:
        return _err("Deal not found.", 404)
    utr = clean_utr((request.get_json(silent=True) or {}).get("utr"))
    if not utr:
        return _err("Enter the UTR of the payment (6 to 30 letters and digits).", 400)
    outbox = Outbox()
    problem_text = mark_paid(deal, user, "lender" if lender_financer_id(user) else "staff",
                             utr, outbox)
    if problem_text:
        db.session.rollback()
        return _err(problem_text, 409)
    failed = _commit()
    if failed:
        return failed
    outbox.send()
    return jsonify(deal.to_dict())
