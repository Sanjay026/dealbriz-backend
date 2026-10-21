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
    if not pincode and data.get("product_id"):
        product = Product.query.get(data["product_id"])
        if product:
            pincode = product.city  # Product.city holds the listing's pincode
    if not pincode:
        pincode = (user.city or "").strip() or None

    availability = resolve_emi_availability(pincode=pincode)
    if not availability.get("available"):
        return None, (jsonify({
            "error": availability.get("message", "EMI is not available in your area yet."),
            "reason": availability.get("reason"),
        }), 403)
    # Snapshot of the listing as it was when the buyer applied. Taken from
    # the database when the listing exists, so the stored title and price
    # are the real ones rather than whatever the client sent.
    product = Product.query.get(data["product_id"]) if data.get("product_id") else None
    title = (product.title if product else data.get("product_title_snapshot")) or ""
    image = (product.image_url if product else data.get("product_image_snapshot")) or None
    price = product.price if product else data.get("product_price_snapshot")
    if price_override is not None:
        price = price_override

    # Every one of these is NOT NULL in emi_applications. Reading them with
    # data["..."] turned a missing field into a KeyError and a bare 500, so
    # the caller never learned what was wrong.
    fields = {
        "tenure_months": data.get("tenure_months"),
        "interest_rate": data.get("interest_rate"),
        "loan_amount": data.get("loan_amount"),
        "monthly_emi": data.get("monthly_emi"),
        "total_payable": data.get("total_payable"),
    }
    missing = [k for k, v in fields.items() if v in (None, "")]
    name = str(data.get("applicant_name") or "").strip()
    phone = str(data.get("applicant_phone") or "").strip()
    if not title:
        missing.append("product_title_snapshot")
    if price in (None, ""):
        missing.append("product_price_snapshot")
    if not name:
        missing.append("applicant_name")
    if not phone:
        missing.append("applicant_phone")
    if missing:
        return None, (jsonify({"error": "Please fill in all the details: " + ", ".join(missing)}), 400)

    try:
        tenure = int(fields["tenure_months"])
        numbers = {k: float(fields[k]) for k in ("interest_rate", "loan_amount", "monthly_emi", "total_payable")}
        price = float(price)
        down = float(data.get("down_payment") or 0)
        income = data.get("applicant_income")
        income = float(income) if income not in (None, "") else None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Some amounts in the application are not valid numbers."}), 400)
    if tenure <= 0 or numbers["loan_amount"] < 0 or price <= 0:
        return None, (jsonify({"error": "Some amounts in the application are not valid."}), 400)
    if price_override is not None and (numbers["loan_amount"] > price or down < 0 or down > price):
        return None, (jsonify({"error": "The loan and down payment can't be more than the agreed price."}), 400)

    emi = EmiApplication(
        user_id=user.id, product_id=product.id if product else None,
        product_title_snapshot=title[:200], product_image_snapshot=image,
        product_price_snapshot=price, down_payment=down, tenure_months=tenure,
        interest_rate=numbers["interest_rate"], loan_amount=numbers["loan_amount"],
        monthly_emi=numbers["monthly_emi"], total_payable=numbers["total_payable"],
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
