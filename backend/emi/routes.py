from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import EmiApplication
from utils import current_user

bp = Blueprint("emi", __name__, url_prefix="/api/emi")


@bp.route("", methods=["POST"])
@jwt_required()
def submit_emi():
    # Enforced here, not just hidden in the UI. Hiding a button stops an
    # ordinary visitor; it does nothing about a crafted request, and an EMI
    # application created while no financing partner exists would be a
    # promise we cannot keep.
    user = current_user()
    data = request.get_json(silent=True) or {}
    emi, error = build_emi_application(user, data)
    if error:
        return error
    db.session.add(emi)
    db.session.commit()
    return jsonify(emi.to_dict()), 201


def build_emi_application(user, data, price_override=None):
    """Validates an application and returns (EmiApplication, None), not yet
    added to the session, or (None, error_response).

    Shared by POST /api/emi and DealLock's POST /api/loans/apply. For a
    DealLock loan, price_override is the deal's agreed price, so the loan is
    sized on what the buyer and seller agreed rather than the listing price.
    """
    # The single gate that decides whether an application may be created.
    # Coverage governs, not the global switch — that switch controls
    # whether EMI is ADVERTISED site-wide, while a pilot region stays live
    # underneath it. Enforced here as well as in the UI, because the
    # frontend popup stops an ordinary user but not a stale page or a
    # crafted request, and an application in an uncovered region is one no
    # partner has agreed to fund.
    from locations.routes import resolve_emi_availability
    from models import Product

    pincode = (data.get("pincode") or "").strip() or None
    from_listing = False
    if not pincode and data.get("product_id"):
        product = Product.query.get(data["product_id"])
        if product:
            pincode = (product.city or "").strip() or None  # Product.city holds the listing's pincode
            from_listing = bool(pincode)
    if not pincode:
        pincode = (user.city or "").strip() or None

    availability = resolve_emi_availability(pincode=pincode)
    # A listing pincode the pincode table doesn't know (a typo, or a new
    # pincode) can't be placed in any state: use the buyer's own instead of
    # refusing outright.
    buyer_pin = (user.city or "").strip() or None
    if (not availability.get("available") and from_listing and availability.get("pincode_known") is False
            and buyer_pin and buyer_pin != pincode):
        availability = resolve_emi_availability(pincode=buyer_pin)
        from_listing = False
    if not availability.get("available"):
        where = availability.get("place")
        checked = availability.get("pincode")
        if checked:
            msg = (f"EMI isn't available yet for {'this listing’s' if from_listing else 'your'} area "
                   f"(pincode {checked}{', ' + where if where else ''}).")
        else:
            msg = availability.get("message", "EMI is not available in your area yet.")
        return None, (jsonify({
            "error": msg,
            "reason": availability.get("reason"),
            "pincode": checked,
        }), 403)
    # Snapshot of the listing as it was when the buyer applied, always from
    # the database. The request used to be able to name any title, price and
    # photo ("product_*_snapshot") and choose every figure; those fields are
    # now ignored and the figures are worked out here.
    product = Product.query.get(data["product_id"]) if data.get("product_id") else None
    if product is None:
        return None, (jsonify({"error": "Choose the listing you're applying for."}), 400)
    if price_override is None and product.status not in ("active", "reserved"):
        return None, (jsonify({"error": "This listing is no longer available."}), 409)
    title = product.title or ""
    image = product.image_url or None
    price = price_override if price_override is not None else product.price

    from deals import rules
    from validation import Invalid, phone as valid_phone, short_text
    try:
        name = short_text(data.get("applicant_name"), "name", 200, required=True)
        phone = valid_phone(data.get("applicant_phone"))
    except Invalid as exc:
        return None, (jsonify({"error": exc.message}), 400)

    try:
        tenure = int(data.get("tenure_months"))
        rate = float(data.get("interest_rate")) if price_override is None else float(rules.LOAN_RATE_MAX)
        price = float(price)
        down = float(data.get("down_payment") or 0)
        income = data.get("applicant_income")
        income = float(income) if income not in (None, "") else None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Some amounts in the application are not valid numbers."}), 400)
    if not (1 <= tenure <= 84) or not (1 <= rate <= 36) or price <= 0:
        return None, (jsonify({"error": "Some amounts in the application are not valid."}), 400)
    if down < 0 or down >= price:
        return None, (jsonify({"error": "The down payment must be less than the price."}), 400)
    if income is not None and not (0 <= income <= 1e9):
        return None, (jsonify({"error": "The monthly income isn't valid."}), 400)
    loan = round(price - down)
    monthly, total = rules.emi_estimate(loan, tenure, rate)

    emi = EmiApplication(
        user_id=user.id, product_id=product.id,
        product_title_snapshot=title[:200], product_image_snapshot=image,
        product_price_snapshot=price, down_payment=down, tenure_months=tenure,
        interest_rate=rate, loan_amount=loan,
        monthly_emi=monthly, total_payable=total,
        applicant_name=name[:200], applicant_phone=phone[:20],
        applicant_income=income, status="pending"
    )
    return emi, None


@bp.route("/mine", methods=["GET"])
@jwt_required()
def my_emi():
    user = current_user()
    apps = EmiApplication.query.filter_by(user_id=user.id).order_by(EmiApplication.applied_at.desc()).all()
    return jsonify([a.to_dict() for a in apps])
