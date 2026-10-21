from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, verify_jwt_in_request

from extensions import db
from models import Report, User, Product
from utils import current_user

bp = Blueprint("reports", __name__, url_prefix="/api/reports")

# Kept in sync with the frontend's REPORT_REASONS.
REPORT_REASONS = {
    "fraud_scam": "Fraud or scam",
    "fake_listing": "Fake or misleading listing",
    "counterfeit": "Counterfeit goods",
    "harassment": "Harassment or abusive behaviour",
    "prohibited_item": "Prohibited item",
    "payment_issue": "Payment problem",
    "other": "Other",
}

# About the listing itself, so only for the person who posted it.
# Kept in sync with the frontend's SELLER_ONLY_REPORT_REASONS.
SELLER_ONLY_REASONS = {"fake_listing", "counterfeit", "prohibited_item"}


@bp.route("", methods=["POST"])
@jwt_required()
def submit_report():
    reporter = current_user()
    data = request.get_json() or {}

    reported_user_id = data.get("reported_user_id")
    reason = data.get("reason")

    if not reported_user_id or not reason:
        return jsonify({"error": "Please choose a reason for the report"}), 400
    if reason not in REPORT_REASONS:
        return jsonify({"error": "Invalid report reason"}), 400
    if reported_user_id == reporter.id:
        return jsonify({"error": "You cannot report yourself"}), 400

    seller = User.query.get(reported_user_id)
    if not seller:
        return jsonify({"error": "That user no longer exists"}), 404

    product_id = data.get("product_id")
    product = Product.query.get(product_id) if product_id else None
    if product_id and not product:
        product_id = None  # listing was removed in the meantime; keep the report

    # Who is being reported, worked out here from the listing rather than
    # taken from the client: "seller" when they posted the listing, "buyer"
    # when the reporter posted it (a seller reporting someone from their
    # chat), "user" when there is no listing to tell (none sent, or deleted).
    if product is not None and product.posted_by == reported_user_id:
        report_type = "seller"
    elif product is not None and product.posted_by == reporter.id:
        report_type = "buyer"
    else:
        report_type = "user"
    if report_type != "seller" and reason in SELLER_ONLY_REASONS:
        return jsonify({"error": "That reason is only for reporting a seller. Please choose another."}), 400

    # Don't let one user pile up duplicate open reports against the same
    # seller — it floods the admin queue without adding information.
    existing = Report.query.filter_by(
        reported_user_id=reported_user_id, reporter_id=reporter.id, status="open"
    ).first()
    if existing:
        return jsonify({
            "error": "You already have an open report against this user. "
                     "Our team is reviewing it."
        }), 409

    report = Report(
        report_type=report_type,
        reported_user_id=reported_user_id,
        reporter_id=reporter.id,
        product_id=product_id,
        reason=reason,
        details=(data.get("details") or "").strip()[:2000],
        status="open",
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({"ok": True, "report_id": report.report_id}), 201


@bp.route("/reasons", methods=["GET"])
def list_reasons():
    return jsonify([{"value": k, "label": v} for k, v in REPORT_REASONS.items()])


# Reasons for a site problem (distinct from reporting a seller — these are
# about DealBridge itself, so they need their own vocabulary).
PROBLEM_REASONS = {
    "bug": "Something is broken",
    "listing_issue": "Problem with a listing",
    "payment_issue": "Payment or EMI problem",
    "account_issue": "Account or login problem",
    "abuse": "Abuse or unsafe behaviour",
    "feedback": "Feedback or suggestion",
    "other": "Something else",
}


@bp.route("/problem-reasons", methods=["GET"])
def list_problem_reasons():
    return jsonify([{"value": k, "label": v} for k, v in PROBLEM_REASONS.items()])


@bp.route("/problem", methods=["POST"])
def submit_problem():
    """"Report a Problem" and "Contact Us" both land here, as reports of
    type `problem`, so staff have one queue rather than a separate contact
    inbox that nobody remembers to check.

    Deliberately NOT @jwt_required: someone who can't log in still needs to
    be able to tell us that. Logged-out reporters must supply an email so
    we can reply."""
    reporter = None
    try:
        verify_jwt_in_request(optional=True)
        reporter = current_user()
    except Exception:
        reporter = None

    data = request.get_json() or {}
    reason = data.get("reason")
    details = (data.get("details") or "").strip()
    subject = (data.get("subject") or "").strip()
    contact_email = (data.get("contact_email") or "").strip()

    if reason not in PROBLEM_REASONS:
        return jsonify({"error": "Please choose what the problem is about"}), 400
    if not details:
        return jsonify({"error": "Please describe the problem"}), 400
    if not reporter and not contact_email:
        return jsonify({"error": "Please provide an email so we can reply"}), 400
    if contact_email and "@" not in contact_email:
        return jsonify({"error": "Please enter a valid email address"}), 400

    # Fall back to the account's email when a logged-in user doesn't type one.
    if reporter and not contact_email:
        contact_email = reporter.email

    product_id = data.get("product_id")
    if product_id and not Product.query.get(product_id):
        product_id = None

    report = Report(
        report_type="problem",
        reported_user_id=None,
        reporter_id=reporter.id if reporter else None,
        product_id=product_id,
        reason=reason,
        subject=subject[:200] or PROBLEM_REASONS[reason],
        contact_email=contact_email[:255],
        details=details[:4000],
        status="open",
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({"ok": True, "report_id": report.report_id}), 201
