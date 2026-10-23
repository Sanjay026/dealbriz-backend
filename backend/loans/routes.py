"""
DealLock loans (phase 2, manual mode).

    GET  /api/loans/lenders?deal_id=   the lenders the buyer may choose for
                                       a locked deal (see buyer_lender_choices)
    POST /api/loans/apply        buyer applies for the loan on a locked deal,
                                 with the lender they chose (financer_id)
    GET  /api/loans/<id>         the loan and its status timeline

A loan here is an EMI application (emi_applications) linked to a deal. Staff
move it through the existing statuses in the admin panel (pending, review,
approved, rejected); admin/routes.py tells the deal when that happens. Ready
to pay and Paid are separate staff steps on the deal (deals/routes.py).

Not built yet, because both need a lender's API spec:
    POST /api/loans/precheck            PAN, OTP and soft credit check
    POST /api/loans/webhook/<financer>  signed status updates from the lender
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Deal, EmiApplication, LoanStatusEvent, Product
from models.deal import (DEAL_AWAITING_SELLER, DEAL_DOCS_PENDING,
                         DEAL_LOAN_IN_PROGRESS, DEAL_LOCKED, LOAN_VEHICLE)
from utils import current_user
from deals.service import (Outbox, add_event, add_loan_event, buyer_lender_choices,
                           financer_name,
                           lender_financer_id, notify, notify_lender,
                           post_thread_message, short_date, utcnow)

bp = Blueprint("loans", __name__, url_prefix="/api/loans")

NO_LENDER_MESSAGE = ("No lending partner is available for this loan yet. "
                     "Please contact DealBriz support.")


def _err(message, code):
    return jsonify({"error": message}), code


@bp.route("/lenders", methods=["GET"])
@jwt_required()
def lenders_for_deal():
    """?deal_id=  The lenders the buyer of this deal may choose on the loan
    form: [{financer_id, name}], nearest first. Names only: a lender's
    documents email and contacts are internal."""
    user = current_user()
    if not user or (user.account_status or "active") != "active":
        return _err("Please sign in again.", 401)
    deal = Deal.query.get(request.args.get("deal_id") or "")
    if not deal or user.id != deal.buyer_id:
        return _err("Deal not found.", 404)
    return jsonify({
        "loan_type": deal.loan_type,
        "lenders": [{"financer_id": f.financer_id, "name": f.name}
                    for f in buyer_lender_choices(deal)],
    })


@bp.route("/apply", methods=["POST"])
@jwt_required()
def apply_for_loan():
    """Start the loan for a locked deal, either by linking an EMI application
    the buyer already sent for this listing:
        {deal_id, financer_id, emi_application_id}
    or by sending the same fields as POST /api/emi, which then sizes the loan
    on the deal's agreed price:
        {deal_id, financer_id, tenure_months, interest_rate, loan_amount,
         monthly_emi, total_payable, down_payment, applicant_name,
         applicant_phone, applicant_income}
    financer_id is the lender the BUYER chose (one of GET /lenders). It
    becomes the application's and the deal's lender, and the documents
    email goes to that lender only. The deal moves to Loan in progress. The
    7-day reservation keeps running until the lender marks it Ready to pay."""
    user = current_user()
    if not user or (user.account_status or "active") != "active":
        return _err("Please sign in again.", 401)
    data = request.get_json(silent=True) or {}

    deal = Deal.query.filter_by(id=data.get("deal_id")).with_for_update().first()
    if not deal or user.id != deal.buyer_id:
        return _err("Deal not found.", 404)
    if deal.status != DEAL_LOCKED:
        if deal.status == DEAL_LOAN_IN_PROGRESS:
            return _err("You've already applied for the loan on this deal.", 409)
        if deal.status == DEAL_AWAITING_SELLER:
            return _err("Wait for the seller to confirm the deal first.", 409)
        if deal.status == DEAL_DOCS_PENDING:
            return _err("The deal locks once the documents are verified. "
                        "You can apply for the loan then.", 409)
        return _err(f"This deal is {deal.status}; a loan can only be applied for on a locked deal.", 409)

    # The lender the buyer chose. Checked against the same list the form
    # showed, so a stale page or a crafted request can't pick a lender that
    # is inactive, doesn't fund this loan type or doesn't serve the area.
    choices = buyer_lender_choices(deal)
    if not choices:
        return _err(NO_LENDER_MESSAGE, 409)
    try:
        chosen_id = int(data.get("financer_id"))
    except (TypeError, ValueError):
        return _err("Choose your lender.", 400)
    chosen = next((f for f in choices if f.financer_id == chosen_id), None)
    if chosen is None:
        return _err("That lender isn't available for this loan. Please choose another.", 409)

    if data.get("emi_application_id"):
        app = (EmiApplication.query.filter_by(id=data["emi_application_id"])
               .with_for_update().first())
        if not app or app.user_id != user.id:
            return _err("EMI application not found.", 404)
        if app.product_id != deal.product_id:
            return _err("That EMI application is for a different listing.", 409)
        if app.deal_id:
            return _err("That EMI application is already linked to a deal.", 409)
        if app.status not in ("pending", "review", "approved"):
            return _err(f"That EMI application is {app.status}. Please apply again.", 409)
        if float(app.loan_amount) > float(deal.agreed_price):
            return _err("That application's loan is more than the agreed price. Please apply again.", 409)
        linked_existing = True
    else:
        from emi.routes import build_emi_application
        payload = dict(data)
        payload["product_id"] = deal.product_id
        app, error = build_emi_application(user, payload, price_override=deal.agreed_price)
        if error:
            return error
        db.session.add(app)
        linked_existing = False

    # The buyer's choice is the lender, for the application (also a linked
    # earlier one) and for the deal.
    app.financer_id = chosen.financer_id

    now = utcnow()
    app.deal_id = deal.id
    db.session.flush()
    deal.status = DEAL_LOAN_IN_PROGRESS
    deal.loan_applied_at = now
    deal.financer_id = chosen.financer_id
    add_loan_event(app, app.status, "buyer", user.id,
                   ("Existing application linked to the deal" if linked_existing
                    else "Applied for this deal") + f", lender chosen by the buyer: {chosen.name}")
    add_event(deal, "loan_applied", user.id, application_id=app.id, lender=chosen.name)

    product = Product.query.get(deal.product_id)
    title = product.title if product else "the listing"
    checks = "the buyer and the vehicle" if deal.loan_type == LOAN_VEHICLE else "the buyer"
    text = (f"The buyer has applied for the loan. The listing stays reserved until "
            f"{short_date(deal.reserved_until)} while the lender checks {checks}.")
    post_thread_message(deal.product_id, user.id, deal.seller_id, text, "system")
    outbox = Outbox()
    notify(deal.seller_id, f"Loan applied · {title}"[:200], text, deal.product_id)
    outbox.push(deal.seller_id, f"Loan applied · {title}", text, deal.product_id, user.id)
    if app.financer_id:
        notify_lender(app.financer_id, f"New loan application \u00b7 {title}"[:200],
                      f"{app.applicant_name} applied for {deal.loan_type} loan on a DealLock deal.",
                      deal.product_id)
    # The lender's email: the application with both sides' documents.
    from deals import lender_email
    mail_row = lender_email.queue(deal, app, user.id)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _err("Something changed while you were doing that. Please refresh and try again.", 409)
    outbox.send()
    if mail_row is not None and mail_row.status == lender_email.PENDING:
        lender_email.send_soon([mail_row.id])
    return jsonify(_loan_view(app, deal)), 201


def _loan_view(app, deal):
    d = app.to_dict()
    d["lender"] = financer_name(app.financer_id)
    d["deal"] = deal.to_dict() if deal else None
    events = (LoanStatusEvent.query.filter_by(application_id=app.id)
              .order_by(LoanStatusEvent.created_at.asc()).all())
    d["events"] = [e.to_dict() for e in events]
    return d


@bp.route("/<app_id>", methods=["GET"])
@jwt_required()
def get_loan(app_id):
    """The buyer's own loan, any loan for staff who can view EMI
    applications, or the lender's own loans. The seller sees loan progress
    on the deal instead (GET /api/deals/<id>), without the terms."""
    user = current_user()
    if not user:
        return _err("Please sign in again.", 401)
    app = EmiApplication.query.get(app_id)
    deal = Deal.query.get(app.deal_id) if app and app.deal_id else None
    from permissions import has_capability
    allowed = app is not None and (
        app.user_id == user.id
        or has_capability(user, "emi.view")
        or (lender_financer_id(user) is not None and app.financer_id == lender_financer_id(user)))
    if not allowed:
        return _err("Loan not found.", 404)
    return jsonify(_loan_view(app, deal))
